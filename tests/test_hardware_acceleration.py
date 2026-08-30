from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

from evidence_lane_plugin.ai_toolchain import resolve_lane_toolchain
from evidence_lane_plugin.hardware_acceleration import (
    HardwareAccelerationProbe,
    resolve_hardware_acceleration,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "evidence-lane-plugin"
RUNTIME_CONTRACT = PLUGIN_ROOT / "scripts" / "runtime_contract.py"


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
    assert directml["selected_provider"] == "AMD_DIRECTML"
    assert directml["runtime_contract"]["directml_sequential_execution_required"] is True
    assert directml["runtime_contract"]["directml_memory_pattern_disabled_required"] is True


def test_cpu_only_action_never_uses_gpu_and_lane_route_carries_receipt() -> None:
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

    route = resolve_lane_toolchain(
        lane_id="local_code",
        host_profile="CODEX_DESKTOP",
        accelerator_profile="nvidia",
        enabled_accelerator_plugins=["nvidia"],
        accelerator_probe=_probe(
            nvidia_detected=True,
            torch_cuda_available=True,
            torch_cuda_version="13.0",
            total_vram_mib=8151,
            used_vram_mib=100,
        ),
    )
    assert route["hardware_acceleration"]["selected_provider"] == "NVIDIA_CUDA"
    assert len(route["hardware_acceleration"]["receipt_sha256"]) == 64


def test_env_selects_accelerators_and_uop_governs_their_budget() -> None:
    env = sqlite3.connect(PLUGIN_ROOT / "env" / "env_sqlite.sqlite")
    uop = sqlite3.connect(PLUGIN_ROOT / "uop" / "uop_sqlite.sqlite")
    try:
        env_rows = list(
            env.execute(
                "SELECT provider_id,vendor_plugin,eligible_action_classes_json,"
                "default_memory_budget_percent,provider_is_tool,provider_is_agent "
                "FROM env_accelerator_profile_v17 ORDER BY provider_id"
            )
        )
        uop_rows = list(
            uop.execute(
                "SELECT provider_id,vendor_plugin_grant_required,"
                "default_memory_budget_percent,max_memory_budget_percent,"
                "telemetry_required,throttle_blocks_execution,cpu_fallback_required,"
                "authority_effect FROM uop_accelerator_policy_v17 ORDER BY provider_id"
            )
        )
    finally:
        env.close()
        uop.close()
    assert {row[0] for row in env_rows} == {
        "CPU",
        "NVIDIA_CUDA",
        "AMD_ROCM",
        "AMD_DIRECTML",
    }
    nvidia = next(row for row in env_rows if row[0] == "NVIDIA_CUDA")
    assert json.loads(nvidia[2]) == ["RETRIEVAL", "OCR_MEDIA", "EVALUATION"]
    assert nvidia[3:] == (80, 0, 0)
    nvidia_policy = next(row for row in uop_rows if row[0] == "NVIDIA_CUDA")
    assert nvidia_policy[1:] == (1, 80, 95, 1, 1, 1, 0)
    assert "ACCELERATORS_NVIDIA_CUDA" in (
        PLUGIN_ROOT / "env" / "env_mmd.mmd"
    ).read_text(encoding="utf-8")
    assert "ACCELERATOR_POLICIES_NVIDIA_CUDA" in (
        PLUGIN_ROOT / "uop" / "uop_mmd.mmd"
    ).read_text(encoding="utf-8")


def test_accelerator_profile_changes_the_durable_runtime_identity(
    monkeypatch,
) -> None:
    spec = importlib.util.spec_from_file_location("runtime_contract_accelerator", RUNTIME_CONTRACT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setenv("EVIDENCE_LANE_ACCELERATOR_PROFILE", "cpu")
    cpu = module.runtime_identity(PLUGIN_ROOT)
    monkeypatch.setenv("EVIDENCE_LANE_ACCELERATOR_PROFILE", "nvidia")
    nvidia = module.runtime_identity(PLUGIN_ROOT)
    monkeypatch.setenv("EVIDENCE_LANE_ACCELERATOR_PROFILE", "amd")
    amd = module.runtime_identity(PLUGIN_ROOT)

    assert len({cpu["runtime_key"], nvidia["runtime_key"], amd["runtime_key"]}) == 3
    assert cpu["selected_torch_lock_sha256"] == cpu["requirements_torch_cpu_lock_sha256"]
    assert nvidia["selected_torch_lock_sha256"] == nvidia["requirements_torch_nvidia_lock_sha256"]
    assert amd["directml_selected"] is True


def test_accelerator_locks_are_exact_and_hash_pinned() -> None:
    nvidia = (PLUGIN_ROOT / "requirements.torch-nvidia.lock.txt").read_text(
        encoding="utf-8"
    )
    directml = (PLUGIN_ROOT / "requirements.onnx-directml.lock.txt").read_text(
        encoding="utf-8"
    )
    assert "--index-url https://download.pytorch.org/whl/cu130" in nvidia
    assert "torch==2.13.0+cu130" in nvidia
    assert "590b2a2b53ef295dcfcd703df17929bd579705bb0e42d9e43c462fe0f0b1e088" in nvidia
    assert "a51e85f6133741ff89a941497dd8dbb208707c68f472c1983ae8dbed728876e1" in nvidia
    assert "onnxruntime-directml==1.24.4" in directml
    assert "--hash=sha256:" in directml
