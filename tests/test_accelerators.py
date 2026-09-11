from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from evidence_lane_plugin.accelerators import (
    AcceleratorConfig,
    AcceleratorService,
    DeviceObservation,
    RuntimeEvidence,
    probe_nvidia_devices,
)
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.hardware_acceleration import (
    HardwareAccelerationProbe,
    probe_hardware_acceleration,
    resolve_hardware_acceleration,
)
from evidence_lane_plugin.projects import ProjectAccess
from evidence_lane_plugin.registry import ActionContext
from evidence_lane_plugin.storage import ProjectStore
from evidence_lane_plugin.writers import WriterLease


@pytest.fixture
def state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    store = ProjectStore.create(tmp_path / "state", source)
    access = ProjectAccess(store)
    access.initialize()
    permissions = frozenset({"admin", "tools", "read"})
    grant = access.issue("client", permissions, [source])
    context = ActionContext("client", store.project_id, permissions)
    current = datetime.now(UTC)
    service = AcceleratorService(store, clock=lambda: current)
    with WriterLease(store, "engine") as lease:
        service.initialize(lease)
        yield service, context, lease, access, grant


def configure(service, context, lease, **changes):
    config = AcceleratorConfig.model_validate({
        "requested_profile": "nvidia", "enabled_vendor_plugins": ["nvidia"],
        "purpose": "Accelerate bounded project retrieval.", "action_classes": ["RETRIEVAL"],
        "device_id": "GPU-test", "expires_at": "NO_EXPIRY",
    } | changes)
    return service.configure(config, context, lease, expected_revision=service.settings()["revision"])


def device(service, **changes):
    return DeviceObservation.model_validate({
        "device_id": "GPU-test", "device_index": 0, "vendor": "nvidia", "name": "NVIDIA fixture",
        "driver_version": "fixture", "total_vram_mib": 8000, "used_vram_mib": 1000,
        "temperature_c": 40, "throttle_active": False, "source": "nvidia_smi",
        "observed_at": service.clock().isoformat(),
    } | changes)


def runtime(service, **changes):
    return RuntimeEvidence.model_validate({
        "provider": "NVIDIA_CUDA", "device_id": "GPU-test", "runtime_version": "fixture",
        "runtime_platform_version": "13.0", "environment_digest": "a" * 64,
        "observed_at": service.clock().isoformat(), "self_test": "passed",
        "probe_id": "fixture-only", "reason": "Test fixture; no hardware execution claim",
    } | changes)


def select(service, context, **changes):
    return service.select(**{"context": context, "action_class": "RETRIEVAL",
                             "devices": [device(service)], "runtimes": [runtime(service)]} | changes)


def test_default_cpu_and_project_specific_configuration(state):
    service, context, lease, _, _ = state
    assert select(service, context)["selected_provider"] == "CPU"
    configured = configure(service, context, lease)
    result = select(service, context)
    assert result["selected_provider"] == "NVIDIA_CUDA"
    assert result["execution_state"] == "not_executed"
    assert result["settings_revision"] == configured["revision"]
    assert result["environment_digest"] == "a" * 64
    assert result["decision"]["admissible_additional_vram_mib"] == 5400
    with pytest.raises(LaneError):
        service.configure(AcceleratorConfig.model_validate(configured["config"]), context, lease, expected_revision=0)


@pytest.mark.parametrize("changes,reason", [
    ({"temperature_c": 90}, "GPU_TEMPERATURE_LIMIT_EXCEEDED_OR_UNAVAILABLE"),
    ({"temperature_c": None}, "GPU_TEMPERATURE_LIMIT_EXCEEDED_OR_UNAVAILABLE"),
    ({"throttle_active": True}, "GPU_THROTTLE_ACTIVE"),
    ({"used_vram_mib": 6500}, "GPU_MEMORY_BUDGET_EXHAUSTED"),
    ({"total_vram_mib": None}, "GPU_MEMORY_TELEMETRY_REQUIRED"),
    ({"throttle_active": None}, "FRESH_DEVICE_TELEMETRY_REQUIRED"),
])
def test_memory_temperature_and_throttle_budgets_have_visible_fallbacks(state, changes, reason):
    service, context, lease, _, _ = state
    configure(service, context, lease)
    result = select(service, context, devices=[device(service, **changes)])
    assert result["selected_provider"] == "CPU"
    assert reason in result["fallback_reasons"]


def test_job_memory_estimate_and_stale_probes_cannot_pass(state):
    service, context, lease, _, _ = state
    configure(service, context, lease)
    result = select(service, context, required_vram_mib=6000)
    assert "OPERATION_EXCEEDS_AVAILABLE_MEMORY_BUDGET" in result["fallback_reasons"]
    assert result["selected_provider"] == "CPU"
    old = (service.clock() - timedelta(seconds=11)).isoformat()
    assert select(service, context, devices=[device(service, observed_at=old)])["selected_provider"] == "CPU"
    old = (service.clock() - timedelta(seconds=301)).isoformat()
    assert select(service, context, runtimes=[runtime(service, observed_at=old)])["selected_provider"] == "CPU"


def test_runtime_device_binding_and_multi_device_ambiguity(state):
    service, context, lease, _, _ = state
    configure(service, context, lease, device_id=None)
    assert select(service, context, runtimes=[runtime(service, device_id="different-device")])["selected_provider"] == "CPU"
    result = select(service, context, devices=[device(service), device(service, device_id="second", device_index=1)])
    assert result["selected_provider"] == "CPU"
    assert "DEVICE_SELECTION_REQUIRED" in result["fallback_reasons"]


def test_grant_expiry_revocation_and_configuration_change_are_enforced(state):
    service, context, lease, access, grant = state
    configure(service, context, lease, expires_at=(service.clock() + timedelta(seconds=1)).isoformat())
    instant = service.clock() + timedelta(seconds=1)
    service.clock = lambda: instant
    assert "ACCELERATOR_GRANT_EXPIRED" in select(service, context)["fallback_reasons"]
    with pytest.raises(LaneError):
        select(service, context, expected_revision=0)
    access.revoke(grant, writer=lease)
    with pytest.raises(LaneError) as error:
        select(service, context)
    assert error.value.code == "PERMISSION_DENIED"


def test_amd_requires_amd_grant_device_identity_and_matching_runtime(state):
    service, context, lease, _, _ = state
    configure(service, context, lease, requested_profile="amd", enabled_vendor_plugins=["amd"], device_id="AMD-test")
    amd = device(service, device_id="AMD-test", vendor="amd", name="AMD fixture", source="amd_smi")
    for provider in ("AMD_ROCM", "DIRECTML"):
        result = select(service, context, devices=[amd], runtimes=[runtime(service, provider=provider, device_id="AMD-test")])
        assert result["selected_provider"] == provider
    assert select(service, context, devices=[amd])["selected_provider"] == "CPU"


def test_directml_uses_an_nvidia_grant_and_exact_nvidia_device(state):
    service, context, lease, _, _ = state
    configure(service, context, lease, action_classes=["OCR_MEDIA"])
    directml = runtime(
        service,
        provider="DIRECTML",
        runtime_platform_version=None,
        device_index=3,
    )
    result = select(
        service,
        context,
        action_class="OCR_MEDIA",
        runtimes=[directml],
    )
    assert result["selected_provider"] == "DIRECTML"
    assert result["runtime_device_index"] == 3


def test_mixed_action_classes_do_not_accelerate_cpu_only_work():
    result = resolve_hardware_acceleration(
        action_classes=["RETRIEVAL", "GOVERNANCE"], requested_profile="nvidia", enabled_vendor_plugins=["nvidia"],
        probe=HardwareAccelerationProbe(os_name="nt", nvidia_detected=True, torch_cuda_available=True,
                                        total_vram_mib=8000, used_vram_mib=1000),
    )
    assert result["selected_provider"] == "CPU"


def test_environment_values_never_become_hardware_telemetry(monkeypatch):
    monkeypatch.setattr("evidence_lane_plugin.hardware_acceleration._nvidia_smi_probe", lambda: None)
    for key in ("TOTAL_VRAM_MIB", "USED_VRAM_MIB", "TEMPERATURE_C"):
        monkeypatch.setenv("EVIDENCE_LANE_ACCELERATOR_" + key, "123")
    observed = probe_hardware_acceleration()
    assert observed.total_vram_mib is None
    assert observed.temperature_c is None
    assert observed.telemetry_sources == []


def test_nvidia_csv_keeps_device_identity_and_ignores_idle_throttle_bit(monkeypatch):
    monkeypatch.setattr("evidence_lane_plugin.accelerators.shutil.which", lambda name: "measured-smi")
    monkeypatch.setattr("evidence_lane_plugin.accelerators.subprocess.run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout="0, GPU-zero, NVIDIA Zero, driver, 8000, 1000, 40, 0x1\n1, GPU-one, NVIDIA One, driver, 12000, 2000, 90, 0x20\n",
    ))
    observed = probe_nvidia_devices()
    assert len(observed) == 2
    assert not observed[0].throttle_active
    assert observed[1].throttle_active
    assert observed[1].device_id == "GPU-one"
