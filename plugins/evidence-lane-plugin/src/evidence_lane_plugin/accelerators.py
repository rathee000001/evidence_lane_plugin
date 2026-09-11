"""Project accelerator grants around the retained v3 budget/fallback policy."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from .errors import LaneError
from .hardware_acceleration import HardwareAccelerationProbe, resolve_hardware_acceleration
from .migrations import Migration, apply_migrations
from .projects import ProjectAccess
from .registry import ActionContext, ActionSpec, Contract
from .storage import LaneStore, ProjectStore, json_text
from .writers import WriterLease

Provider = Literal["NVIDIA_CUDA", "AMD_ROCM", "DIRECTML"]


def aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("A timezone is required")
    return parsed


class AcceleratorConfig(Contract):
    requested_profile: Literal["cpu", "auto", "nvidia", "amd"] = "cpu"
    enabled_vendor_plugins: list[Literal["nvidia", "amd"]] = Field(default_factory=list, max_length=2)
    purpose: str = Field(min_length=1, max_length=500)
    action_classes: list[Literal["RETRIEVAL", "OCR_MEDIA", "EVALUATION"]] = Field(min_length=1, max_length=3)
    device_id: str | None = Field(default=None, min_length=1, max_length=128)
    memory_budget_percent: int = Field(default=80, ge=1, le=95)
    temperature_limit_c: int = Field(default=83, ge=30, le=110)
    expires_at: str

    @field_validator("expires_at")
    @classmethod
    def valid_expiry(cls, value):
        if value != "NO_EXPIRY":
            aware(value)
        return value


class DeviceObservation(Contract):
    device_id: str = Field(min_length=1, max_length=128)
    device_index: int = Field(ge=0, le=255)
    vendor: Literal["nvidia", "amd"]
    name: str = Field(max_length=256)
    driver_version: str | None = Field(default=None, max_length=128)
    total_vram_mib: int | None = Field(default=None, ge=1)
    used_vram_mib: int | None = Field(default=None, ge=0)
    temperature_c: int | None = Field(default=None, ge=-50, le=200)
    throttle_active: bool | None = None
    source: Literal["nvidia_smi", "amd_smi", "amd_adlx", "dxgi", "isolated_runtime"]
    telemetry_limits: list[str] = Field(default_factory=list, max_length=10)
    observed_at: str

    @model_validator(mode="after")
    def valid_measurement(self):
        aware(self.observed_at)
        if self.total_vram_mib is not None and self.used_vram_mib is not None and self.used_vram_mib > self.total_vram_mib:
            raise ValueError("Reported memory usage exceeds the device capacity")
        return self


class RuntimeEvidence(Contract):
    provider: Provider
    device_id: str
    device_index: int | None = Field(default=None, ge=0, le=255)
    runtime_version: str
    runtime_platform_version: str | None = None
    environment_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: str
    self_test: Literal["passed", "failed", "unavailable"]
    probe_id: str
    reason: str


class AcceleratorRead(Contract):
    pass


class AcceleratorConfigure(Contract):
    config: AcceleratorConfig
    expected_revision: int = Field(ge=0)


class AcceleratorSettings(Contract):
    revision: int
    digest: str | None = None
    config: AcceleratorConfig | None = None
    requested_profile: str
    backend_selection_verified: Literal[False] = False


def register_accelerator_actions(engine):
    def result(values):
        config = values.get('config')
        return AcceleratorSettings(revision=values['revision'], digest=values.get('digest'), config=config,
            requested_profile=config['requested_profile'] if config else 'cpu')

    def read(context, request):
        store = engine.directory.open(context.project_id)
        with store.lane('receipts').connection(read_only=True) as connection:
            present = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='accelerator_settings' AND type='table'").fetchone()
        return result(AcceleratorService(store).settings() if present else {'revision':0, 'config':None})

    def configure(context, request):
        store = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(store) as lease:
            service = AcceleratorService(store)
            service.initialize(lease)
            return result(service.configure(request.config, context, lease, expected_revision=request.expected_revision))

    engine.registry.register(ActionSpec('accelerator_read', 'Read the project compute preference without claiming a successful backend selection.',
        AcceleratorRead, AcceleratorSettings, read, profile='runtime', workflow='select-project-tools', studio_read=True))
    engine.registry.register(ActionSpec('accelerator_configure', 'Version the project compute preference with an exact current settings revision.',
        AcceleratorConfigure, AcceleratorSettings, configure, permission='admin', profile='runtime', workflow='select-project-tools', mutates=True))


def probe_nvidia_devices() -> list[DeviceObservation]:
    """Read driver telemetry for every device; never alter clocks or driver settings."""
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return []
    try:
        result = subprocess.run(
            [executable, "--query-gpu=index,uuid,name,driver_version,memory.total,memory.used,temperature.gpu,clocks_throttle_reasons.active",
             "--format=csv,noheader,nounits"], capture_output=True, check=False, timeout=5,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode or len(result.stdout) > 65_536:
            return []
        devices: list[DeviceObservation] = []
        for fields in csv.reader(io.StringIO(result.stdout)):
            if len(fields) != 8 or len(devices) >= 32:
                return []
            index, identity, name, driver, total, used, temperature, throttle = [item.strip() for item in fields]

            def number(value):
                return int(value) if value.isdigit() else None

            devices.append(DeviceObservation(
                device_id=identity, device_index=int(index), vendor="nvidia", name=name,
                driver_version=driver, total_vram_mib=number(total), used_vram_mib=number(used),
                temperature_c=number(temperature),
                throttle_active=bool(int(throttle, 16) & 0xE8) if throttle.startswith("0x") else None,
                source="nvidia_smi", observed_at=datetime.now(UTC).isoformat(),
            ))
        return devices
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def probe_amd_dxgi_devices() -> list[DeviceObservation]:
    from .provider_probe import ProbeUnavailable, enumerate_dxgi

    try:
        return [DeviceObservation(
            device_id=item["device_id"], device_index=item["device_index"], vendor="amd",
            name=item["name"], total_vram_mib=item["total_vram_mib"],
            source="dxgi", observed_at=datetime.now(UTC).isoformat(),
        ) for item in enumerate_dxgi() if item["vendor_id"] == 0x1002]
    except (OSError, ValueError, ProbeUnavailable):
        return []


def probe_amd_devices() -> list[DeviceObservation]:
    """Join current ADLX metrics to exact DXGI LUIDs, with no index guessing."""
    import sys
    from pathlib import Path

    devices = probe_amd_dxgi_devices()
    if not devices:
        return []
    try:
        completed = subprocess.run([sys.executable, '-I', str(Path(__file__).with_name('amd_adlx.py'))],
            capture_output=True, check=False, timeout=5,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if completed.returncode or len(completed.stdout) > 65_536:
            return devices
        response = json.loads(completed.stdout)
        rows = response['devices']
        if response['status'] != 'ok' or len(rows) > 32 or len({row['device_id'] for row in rows}) != len(rows):
            return devices
        metrics = {row['device_id']: row for row in rows}
        return [DeviceObservation.model_validate(device.model_dump() | metrics[device.device_id]
            | {'source': 'amd_adlx', 'observed_at': datetime.now(UTC).isoformat()})
            if device.device_id in metrics else device for device in devices]
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return devices


ACCELERATOR_MIGRATIONS = (
    Migration("accelerator", 1, "Project accelerator configuration history", (
        """CREATE TABLE accelerator_settings (
            revision INTEGER PRIMARY KEY, config_json TEXT NOT NULL CHECK(json_valid(config_json)),
            digest TEXT NOT NULL, actor_id TEXT NOT NULL, configured_at TEXT NOT NULL)""",
    )),
)


class AcceleratorService:
    def __init__(self, store: ProjectStore | LaneStore, *, clock=None):
        self.project = store.project if isinstance(store, LaneStore) else store
        self.store = self.project.lane('receipts')
        self.clock = clock or (lambda: datetime.now(UTC))
        self.access = ProjectAccess(store, clock=self.clock)

    def initialize(self, lease: WriterLease) -> None:
        self._lease(lease)
        apply_migrations(self.store, ACCELERATOR_MIGRATIONS, writer=lease)
        lease.check()

    def _lease(self, lease):
        if lease.store.root != self.project.root:
            raise LaneError("PROJECT_BINDING_MISMATCH", "The writer belongs to another project.")
        lease.check()

    def _authorize(self, context: ActionContext, permission: str):
        if context.project_id != self.store.project_id:
            raise LaneError("PROJECT_BINDING_MISMATCH", "The accelerator configuration belongs to another project.")
        if permission not in context.permissions:
            raise LaneError("PERMISSION_DENIED", "The client lacks the requested accelerator permission.")
        self.access.authorize(context.client_id, permission)

    def configure(self, config: AcceleratorConfig, context: ActionContext, lease: WriterLease,
                  *, expected_revision: int) -> dict:
        self._lease(lease)
        self._authorize(context, "admin")
        config = AcceleratorConfig.model_validate(config.model_dump())
        if config.expires_at != "NO_EXPIRY" and aware(config.expires_at) <= self.clock():
            raise LaneError("ACCELERATOR_GRANT_EXPIRED", "Select a future expiry for this grant.")
        payload = config.model_dump(mode="json")
        digest = hashlib.sha256(json_text(payload).encode()).hexdigest()
        with lease.transaction('receipts') as connection:
            self._authorize(context, "admin")
            previous = connection.execute("SELECT revision FROM accelerator_settings ORDER BY revision DESC LIMIT 1").fetchone()
            current = previous[0] if previous else 0
            if current != expected_revision:
                raise LaneError("ACCELERATOR_REVISION_CONFLICT", "Refresh the accelerator settings before changing them.")
            connection.execute("INSERT INTO accelerator_settings VALUES(?,?,?,?,?)",
                               (current + 1, json_text(payload), digest, context.client_id, self.clock().isoformat()))
            self.store.append_receipt("accelerator_configured", {"revision": current + 1, "digest": digest}, connection=connection)
        return {"revision": current + 1, "digest": digest, "config": payload}

    def settings(self) -> dict:
        with self.store.connection(read_only=True) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='accelerator_settings' AND type='table'").fetchone():
                return {"revision": 0, "digest": None, "config": None, "effective_profile": "cpu"}
            row = connection.execute("SELECT * FROM accelerator_settings ORDER BY revision DESC LIMIT 1").fetchone()
        if row is None:
            return {"revision": 0, "config": None, "effective_profile": "cpu"}
        config = json.loads(row["config_json"])
        if hashlib.sha256(json_text(config).encode()).hexdigest() != row['digest']:
            raise LaneError('ACCELERATOR_SETTINGS_INTEGRITY', 'The current compute settings failed their digest check.')
        AcceleratorConfig.model_validate(config)
        return {"revision": row["revision"], "digest": row["digest"], "config": config,
                "effective_profile": config["requested_profile"]}

    def select(self, *, context: ActionContext, action_class: str, devices: list[DeviceObservation],
               runtimes: list[RuntimeEvidence], required_vram_mib: int = 0,
               expected_revision: int | None = None) -> dict:
        """Engine-only selection; probes and required memory are not public tool input."""
        self._authorize(context, "tools")
        if not 0 <= required_vram_mib <= 1_048_576:
            raise LaneError("INVALID_MEMORY_ESTIMATE", "Provide a bounded operation memory estimate.")
        settings = self.settings()
        if expected_revision is not None and expected_revision != settings["revision"]:
            raise LaneError("ACCELERATOR_REVISION_CONFLICT", "Accelerator settings changed before execution.")
        config = AcceleratorConfig.model_validate(settings["config"]) if settings["config"] else None
        reasons: list[str] = []
        default = HardwareAccelerationProbe(os_name=os.name)

        def result(probe=default, device=None, runtime=None):
            decision = resolve_hardware_acceleration(
                action_classes=[action_class], requested_profile=config.requested_profile if config else "cpu",
                enabled_vendor_plugins=config.enabled_vendor_plugins if config else [],
                memory_budget_percent=config.memory_budget_percent if config else 80,
                temperature_limit_c=config.temperature_limit_c if config else 83, probe=probe,
            )
            return {"selection_id": str(uuid4()), "settings_revision": settings["revision"],
                    "settings_digest": settings.get('digest'),
                    "selected_provider": decision["selected_provider"], "device_id": device.device_id if device else None,
                    "environment_digest": runtime.environment_digest if runtime else None,
                    "runtime_device_index": runtime.device_index if runtime else None,
                    "runtime_probe_id": runtime.probe_id if runtime else None,
                    "execution_state": "not_executed", "decision": decision,
                    "fallback_reasons": list(dict.fromkeys(reasons + decision["fallback_reasons"]))}

        if config is None or config.requested_profile == "cpu":
            return result()
        if config.expires_at != "NO_EXPIRY" and aware(config.expires_at) <= self.clock():
            reasons.append("ACCELERATOR_GRANT_EXPIRED")
            return result()
        if action_class not in config.action_classes:
            reasons.append("ACTION_CLASS_NOT_GRANTED")
            return result()
        candidates = [device for device in devices
                      if device.vendor in config.enabled_vendor_plugins
                      and config.requested_profile in {"auto", device.vendor}
                      and (config.device_id is None or device.device_id == config.device_id)]
        if len(candidates) != 1:
            reasons.append("DEVICE_SELECTION_REQUIRED" if candidates else "GRANTED_DEVICE_UNAVAILABLE")
            return result()
        device = candidates[0]
        age = (self.clock() - aware(device.observed_at)).total_seconds()
        if age < 0 or age > 10 or (device.throttle_active is None and device.source != 'amd_adlx'):
            reasons.append("FRESH_DEVICE_TELEMETRY_REQUIRED")
            return result()
        for runtime in runtimes:
            runtime_age = (self.clock() - aware(runtime.observed_at)).total_seconds()
            vendor = (
                "nvidia"
                if runtime.provider == "NVIDIA_CUDA"
                else "amd"
                if runtime.provider == "AMD_ROCM"
                else device.vendor
            )
            if runtime.device_id != device.device_id or vendor != device.vendor:
                continue
            if runtime.self_test != "passed" or runtime_age < 0 or runtime_age > 300:
                continue
            probe = HardwareAccelerationProbe(
                os_name=os.name, device_name=device.name, driver_version=device.driver_version,
                total_vram_mib=device.total_vram_mib, used_vram_mib=device.used_vram_mib,
                temperature_c=device.temperature_c, throttle_active=device.throttle_active,
                nvidia_detected=device.vendor == "nvidia", amd_detected=device.vendor == "amd",
                torch_cuda_available=runtime.provider == "NVIDIA_CUDA", torch_cuda_version=runtime.runtime_platform_version,
                torch_hip_available=runtime.provider == "AMD_ROCM", torch_hip_version=runtime.runtime_platform_version,
                onnx_execution_providers=["DmlExecutionProvider"] if runtime.provider == "DIRECTML" else [],
                telemetry_sources=[device.source, runtime.probe_id],
            )
            chosen = result(probe, device, runtime)
            available = chosen["decision"]["admissible_additional_vram_mib"]
            if chosen["selected_provider"] != "CPU" and available is not None and available >= required_vram_mib:
                return chosen
            reasons.extend(chosen["fallback_reasons"])
            if available is not None and available < required_vram_mib:
                reasons.append("OPERATION_EXCEEDS_AVAILABLE_MEMORY_BUDGET")
        reasons.append("COMPATIBLE_RUNTIME_OR_BUDGET_UNAVAILABLE")
        return result()
