"""Component assertions adapted from the captured v3 hardware tests."""

from __future__ import annotations

from evidence_lane_plugin.hardware_acceleration import (
    HardwareAccelerationProbe,
    resolve_hardware_acceleration,
)


def _probe(**overrides: object) -> HardwareAccelerationProbe:
    values: dict[str, object] = {
        "os_name": "nt",
        "device_name": None,
        "driver_version": None,
        "total_vram_mib": None,
        "used_vram_mib": None,
        "temperature_c": None,
        "throttle_active": False,
        "nvidia_detected": False,
        "amd_detected": False,
        "torch_cuda_available": False,
        "torch_cuda_version": None,
        "torch_hip_available": False,
        "torch_hip_version": None,
        "onnx_execution_providers": ["CPUExecutionProvider"],
        "telemetry_sources": ["TEST_PROBE"],
    }
    values.update(overrides)
    return HardwareAccelerationProbe.model_validate(values)


def test_cpu_is_the_universal_default() -> None:
    result = resolve_hardware_acceleration(
        action_classes=["RETRIEVAL"],
        probe=_probe(),
    )
    assert result["status"] == "PASS_CPU_BASELINE"
    assert result["selected_provider"] == "CPU"
    assert result["cpu_fallback_available"] is True
    assert result["authority_or_hil_effect"] is False


def test_nvidia_uses_eighty_percent_vram_budget_without_forcing_utilization() -> None:
    probe = _probe(
        device_name="NVIDIA GeForce RTX 5060",
        driver_version="591.74",
        total_vram_mib=8151,
        used_vram_mib=2276,
        temperature_c=44,
        nvidia_detected=True,
        torch_cuda_available=True,
        torch_cuda_version="13.0",
    )
    result = resolve_hardware_acceleration(
        action_classes=["RETRIEVAL"],
        requested_profile="nvidia",
        enabled_vendor_plugins=["nvidia"],
        memory_budget_percent=80,
        temperature_limit_c=83,
        probe=probe,
    )
    assert result["status"] == "PASS"
    assert result["selected_provider"] == "NVIDIA_CUDA"
    assert result["budget_vram_mib"] == 6520
    assert result["admissible_additional_vram_mib"] == 4244
    assert result["forced_gpu_utilization_percent"] is None


def test_missing_nvidia_runtime_falls_back_visibly() -> None:
    result = resolve_hardware_acceleration(
        action_classes=["RETRIEVAL"],
        requested_profile="nvidia",
        enabled_vendor_plugins=["nvidia"],
        probe=_probe(nvidia_detected=True, total_vram_mib=8151, used_vram_mib=100),
    )
    assert result["status"] == "PASS_CPU_FALLBACK"
    assert result["selected_provider"] == "CPU"
    assert "NVIDIA_CUDA_RUNTIME_UNAVAILABLE" in result["fallback_reasons"]


def test_amd_rocm_and_directml_are_condition_bound() -> None:
    rocm = resolve_hardware_acceleration(
        action_classes=["EVALUATION"],
        requested_profile="amd",
        enabled_vendor_plugins=["amd"],
        probe=_probe(
            device_name="AMD Radeon",
            total_vram_mib=16384,
            used_vram_mib=2048,
            amd_detected=True,
            torch_hip_available=True,
            torch_hip_version="7.2",
        ),
    )
    assert rocm["selected_provider"] == "AMD_ROCM"

    directml = resolve_hardware_acceleration(
        action_classes=["OCR_MEDIA"],
        requested_profile="amd",
        enabled_vendor_plugins=["amd"],
        probe=_probe(
            device_name="AMD Radeon",
            total_vram_mib=8192,
            used_vram_mib=1024,
            amd_detected=True,
            onnx_execution_providers=["DmlExecutionProvider", "CPUExecutionProvider"],
        ),
    )
    assert directml["selected_provider"] == "DIRECTML"
    assert directml["runtime_contract"]["directml_sequential_execution_required"] is True
    assert directml["runtime_contract"]["directml_memory_pattern_disabled_required"] is True


def test_directml_accepts_a_compatible_nvidia_runtime() -> None:
    directml = resolve_hardware_acceleration(
        action_classes=["OCR_MEDIA"],
        requested_profile="nvidia",
        enabled_vendor_plugins=["nvidia"],
        probe=_probe(
            device_name="NVIDIA fixture",
            total_vram_mib=8192,
            used_vram_mib=1024,
            nvidia_detected=True,
            onnx_execution_providers=["DmlExecutionProvider"],
        ),
    )
    assert directml["selected_provider"] == "DIRECTML"
    assert "NVIDIA_CUDA_RUNTIME_UNAVAILABLE" in directml["fallback_reasons"]


def test_cpu_only_action_never_uses_gpu() -> None:
    decision = resolve_hardware_acceleration(
        action_classes=["GOVERNANCE"],
        requested_profile="nvidia",
        enabled_vendor_plugins=["nvidia"],
        probe=_probe(
            nvidia_detected=True,
            torch_cuda_available=True,
            total_vram_mib=8151,
            used_vram_mib=100,
        ),
    )
    assert decision["selected_provider"] == "CPU"
    assert decision["fallback_reasons"] == ["ACTION_CLASS_CPU_ONLY"]


