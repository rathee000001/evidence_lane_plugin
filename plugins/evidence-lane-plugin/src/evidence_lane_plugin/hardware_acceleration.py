"""Provider-neutral opt-in selection, adapted from the v3 acceleration policy.

This module makes a routing decision. Actual provider execution and per-project
grants are checked by the accelerator service and its isolated runtime probes.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - fixed local hardware probes only
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .hashing import canonical_json_bytes, sha256_bytes

HARDWARE_ACCELERATION_SCHEMA = "evidence-lane.hardware-acceleration-decision.v4"
ACCELERATOR_PROFILES = ("cpu", "auto", "nvidia", "amd")
ACCELERATOR_ELIGIBLE_ACTION_CLASSES = frozenset(
    {"RETRIEVAL", "OCR_MEDIA", "EVALUATION"}
)
_VENDOR_PLUGIN_IDS = frozenset({"nvidia", "amd"})


class HardwareAccelerationProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    os_name: str
    device_name: str | None = None
    driver_version: str | None = None
    total_vram_mib: int | None = Field(default=None, ge=1)
    used_vram_mib: int | None = Field(default=None, ge=0)
    temperature_c: int | None = Field(default=None, ge=-50, le=200)
    throttle_active: bool | None = None
    nvidia_detected: bool = False
    amd_detected: bool = False
    torch_cuda_available: bool = False
    torch_cuda_version: str | None = None
    torch_hip_available: bool = False
    torch_hip_version: str | None = None
    onnx_execution_providers: list[str] = Field(default_factory=list)
    telemetry_sources: list[str] = Field(default_factory=list)


class HardwareAccelerationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_id: Literal["evidence-lane.hardware-acceleration-decision.v4"] = Field(
        alias="schema"
    )
    status: str
    requested_profile: str
    selected_provider: str
    vendor_plugin_grants: list[str]
    action_classes: list[str]
    eligible_action_classes: list[str]
    gpu_action_eligible: bool
    device_name: str | None
    driver_version: str | None
    runtime_ready: bool
    runtime_contract: dict[str, Any]
    memory_budget_percent: int
    total_vram_mib: int | None
    used_vram_mib: int | None
    budget_vram_mib: int | None
    admissible_additional_vram_mib: int | None
    temperature_c: int | None
    temperature_limit_c: int | None
    throttle_active: bool | None
    fallback_reasons: list[str]
    cpu_fallback_available: bool
    forced_gpu_utilization_percent: None
    codex_is_sole_acting_agent: bool
    authority_or_hil_effect: bool
    telemetry_sources: list[str]
    receipt_sha256: str
    execution_state: Literal["not_executed"] = "not_executed"


def _nvidia_smi_probe() -> dict[str, Any] | None:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    try:
        completed = subprocess.run(  # nosec B603
            [executable,
             "--query-gpu=name,driver_version,memory.total,memory.used,temperature.gpu,clocks_throttle_reasons.active",
             "--format=csv,noheader,nounits"],
            check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    line = next((value.strip() for value in completed.stdout.splitlines() if value.strip()), "")
    fields = [value.strip() for value in line.split(",")]
    if len(fields) != 6:
        return None
    try:
        total = int(fields[2])
        used = int(fields[3])
        temperature = int(fields[4])
        # Idle/application clock limiting is not thermal or hardware throttling.
        throttle = bool(int(fields[5], 16) & 0xE8)
    except ValueError:
        return None
    return {
        "device_name": fields[0],
        "driver_version": fields[1],
        "total_vram_mib": total,
        "used_vram_mib": used,
        "temperature_c": temperature,
        "throttle_active": throttle,
    }


def probe_hardware_acceleration() -> HardwareAccelerationProbe:
    # Read-only inventory only. Heavy or incompatible runtimes stay outside the
    # persistent engine; their isolated probes must verify a real operation.
    nvidia = _nvidia_smi_probe()
    return HardwareAccelerationProbe(
        os_name=os.name,
        device_name=str(nvidia["device_name"]) if nvidia else None,
        driver_version=(str(nvidia["driver_version"]) if nvidia else None),
        total_vram_mib=(int(nvidia["total_vram_mib"]) if nvidia else None),
        used_vram_mib=(int(nvidia["used_vram_mib"]) if nvidia else None),
        temperature_c=(int(nvidia["temperature_c"]) if nvidia else None),
        throttle_active=bool(nvidia and nvidia["throttle_active"]),
        nvidia_detected=nvidia is not None,
        telemetry_sources=["NVIDIA_SMI"] if nvidia else [],
    )


def resolve_hardware_acceleration(
    *,
    action_classes: list[str] | tuple[str, ...],
    requested_profile: str = "cpu",
    enabled_vendor_plugins: list[str] | tuple[str, ...] = (),
    memory_budget_percent: int = 80,
    temperature_limit_c: int | None = None,
    probe: HardwareAccelerationProbe | None = None,
) -> dict[str, Any]:
    exact_profile = requested_profile.strip().lower()
    if exact_profile not in ACCELERATOR_PROFILES:
        raise ValueError("HARDWARE_ACCELERATOR_PROFILE_UNSUPPORTED")
    if not 1 <= int(memory_budget_percent) <= 95:
        raise ValueError("HARDWARE_ACCELERATOR_MEMORY_BUDGET_INVALID")
    if temperature_limit_c is not None and not 30 <= int(temperature_limit_c) <= 110:
        raise ValueError("HARDWARE_ACCELERATOR_TEMPERATURE_LIMIT_INVALID")
    grants = sorted({str(value).strip().lower() for value in enabled_vendor_plugins if str(value).strip()})
    unknown = sorted(set(grants) - _VENDOR_PLUGIN_IDS)
    if unknown:
        raise ValueError("HARDWARE_ACCELERATOR_VENDOR_PLUGIN_UNSUPPORTED")
    exact_actions = list(dict.fromkeys(str(value).strip().upper() for value in action_classes))
    # A mixed request is not GPU eligible merely because one of its classes is.
    eligible = exact_actions if exact_actions and set(exact_actions) <= ACCELERATOR_ELIGIBLE_ACTION_CLASSES else []
    observed = probe or probe_hardware_acceleration()
    reasons: list[str] = []
    selected = "CPU"
    runtime_ready = False

    if not eligible:
        reasons.append("ACTION_CLASS_CPU_ONLY")
    elif exact_profile == "cpu":
        reasons.append("CPU_PROFILE_SELECTED")
    else:
        candidates: list[str] = []
        if exact_profile in {"auto", "nvidia"} and "nvidia" in grants:
            candidates.extend(["NVIDIA_CUDA", "DIRECTML"])
        if exact_profile in {"auto", "amd"} and "amd" in grants:
            candidates.extend(["AMD_ROCM", "DIRECTML"])
        candidates = list(dict.fromkeys(candidates))
        if exact_profile in {"nvidia", "amd"} and exact_profile not in grants:
            reasons.append("VENDOR_PLUGIN_GRANT_REQUIRED")
        for candidate in candidates:
            if candidate == "NVIDIA_CUDA":
                if not observed.nvidia_detected:
                    reasons.append("NVIDIA_HARDWARE_OR_DRIVER_UNAVAILABLE")
                    continue
                if not observed.torch_cuda_available:
                    reasons.append("NVIDIA_CUDA_RUNTIME_UNAVAILABLE")
                    continue
            elif candidate == "AMD_ROCM":
                if not observed.amd_detected or not observed.torch_hip_available:
                    reasons.append("AMD_ROCM_RUNTIME_UNAVAILABLE")
                    continue
            elif candidate == "DIRECTML":
                if observed.os_name != "nt":
                    reasons.append("DIRECTML_REQUIRES_WINDOWS")
                    continue
                if (
                    not (observed.nvidia_detected or observed.amd_detected)
                    or "DmlExecutionProvider" not in observed.onnx_execution_providers
                ):
                    reasons.append("DIRECTML_RUNTIME_UNAVAILABLE")
                    continue
            if observed.total_vram_mib is None or observed.used_vram_mib is None:
                reasons.append("GPU_MEMORY_TELEMETRY_REQUIRED")
                continue
            if observed.throttle_active:
                reasons.append("GPU_THROTTLE_ACTIVE")
                continue
            if temperature_limit_c is not None and (
                observed.temperature_c is None
                or observed.temperature_c > int(temperature_limit_c)
            ):
                reasons.append("GPU_TEMPERATURE_LIMIT_EXCEEDED_OR_UNAVAILABLE")
                continue
            budget = int(observed.total_vram_mib * int(memory_budget_percent) / 100)
            if budget <= observed.used_vram_mib:
                reasons.append("GPU_MEMORY_BUDGET_EXHAUSTED")
                continue
            selected = candidate
            runtime_ready = True
            break
        if not candidates and not reasons:
            reasons.append("NO_ENABLED_VENDOR_PLUGIN")

    budget_vram = (
        int(observed.total_vram_mib * int(memory_budget_percent) / 100)
        if observed.total_vram_mib is not None
        else None
    )
    admissible = (
        max(0, budget_vram - int(observed.used_vram_mib))
        if budget_vram is not None and observed.used_vram_mib is not None
        else None
    )
    if selected != "CPU":
        status = "PASS"
    elif exact_profile == "cpu" or not eligible:
        status = "PASS_CPU_BASELINE"
    else:
        status = "PASS_CPU_FALLBACK"
    runtime_contract = {
        "torch_cuda_available": observed.torch_cuda_available,
        "torch_cuda_version": observed.torch_cuda_version,
        "torch_hip_available": observed.torch_hip_available,
        "torch_hip_version": observed.torch_hip_version,
        "onnx_execution_providers": observed.onnx_execution_providers,
        "directml_sequential_execution_required": selected == "DIRECTML",
        "directml_memory_pattern_disabled_required": selected == "DIRECTML",
    }
    core = {
        "schema": HARDWARE_ACCELERATION_SCHEMA,
        "status": status,
        "requested_profile": exact_profile.upper(),
        "selected_provider": selected,
        "vendor_plugin_grants": grants,
        "action_classes": exact_actions,
        "eligible_action_classes": eligible,
        "gpu_action_eligible": bool(eligible),
        "device_name": observed.device_name,
        "driver_version": observed.driver_version,
        "runtime_ready": runtime_ready,
        "runtime_contract": runtime_contract,
        "memory_budget_percent": int(memory_budget_percent),
        "total_vram_mib": observed.total_vram_mib,
        "used_vram_mib": observed.used_vram_mib,
        "budget_vram_mib": budget_vram,
        "admissible_additional_vram_mib": admissible,
        "temperature_c": observed.temperature_c,
        "temperature_limit_c": temperature_limit_c,
        "throttle_active": observed.throttle_active,
        "fallback_reasons": list(dict.fromkeys(reasons)),
        "cpu_fallback_available": True,
        "forced_gpu_utilization_percent": None,
        "codex_is_sole_acting_agent": True,
        "authority_or_hil_effect": False,
        "telemetry_sources": observed.telemetry_sources,
    }
    decision = HardwareAccelerationDecision(
        **core,
        receipt_sha256=sha256_bytes(canonical_json_bytes(core)),
    )
    return decision.model_dump(mode="json", by_alias=True)


def hardware_acceleration_catalog() -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.hardware-acceleration-catalog.v4",
        "status": "PASS",
        "profiles": list(ACCELERATOR_PROFILES),
        "eligible_action_classes": sorted(ACCELERATOR_ELIGIBLE_ACTION_CLASSES),
        "providers": ["CPU", "NVIDIA_CUDA", "AMD_ROCM", "DIRECTML"],
        "default_profile": "CPU",
        "default_memory_budget_percent": 80,
        "memory_budget_is_not_forced_utilization": True,
        "user_enabled_vendor_plugin_required": True,
        "cpu_fallback_required": True,
        "provider_is_not_tool_or_authority": True,
        "official_compatibility_sources": [
            "https://pytorch.org/get-started/locally/",
            "https://docs.nvidia.com/deploy/cuda-compatibility/latest/",
            "https://rocm.docs.amd.com/en/latest/compatibility/compatibility-matrix.html",
            "https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html",
        ],
    }
    return {**body, "catalog_sha256": sha256_bytes(canonical_json_bytes(body))}


def hardware_acceleration_schema() -> dict[str, Any]:
    return HardwareAccelerationDecision.model_json_schema(by_alias=True)


__all__ = [
    "ACCELERATOR_ELIGIBLE_ACTION_CLASSES",
    "ACCELERATOR_PROFILES",
    "HARDWARE_ACCELERATION_SCHEMA",
    "HardwareAccelerationDecision",
    "HardwareAccelerationProbe",
    "hardware_acceleration_catalog",
    "hardware_acceleration_schema",
    "probe_hardware_acceleration",
    "resolve_hardware_acceleration",
]
