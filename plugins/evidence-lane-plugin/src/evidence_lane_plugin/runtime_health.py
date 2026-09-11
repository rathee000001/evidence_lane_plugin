"""Measured runtime health, adapted from v3 runtime_toolchain inspection.

Inventory, installed environments, provider probes and job execution remain
separate observations. Reading status never probes a GPU or mutates a project.
"""

from __future__ import annotations

import copy
import importlib.util
import shutil
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import Field

from .accelerators import probe_amd_devices, probe_nvidia_devices
from .errors import LaneError
from .optional_runtimes import OptionalRuntime
from .registry import ActionContext, Contract

if TYPE_CHECKING:
    from .provider_workers import ProviderWorkers


@dataclass(frozen=True)
class ToolRequirement:
    name: str
    modules: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()


CORE_TOOLS = (
    ToolRequirement("mcp", ("mcp",)), ToolRequirement("pydantic", ("pydantic",)),
    ToolRequirement("httpx", ("httpx",)), ToolRequirement("safe_xml", ("defusedxml",)),
    ToolRequirement("git", commands=("git",)),
)


def _age(timestamp, current: datetime) -> float | None:
    try:
        return (current - datetime.fromisoformat(timestamp)).total_seconds()
    except (TypeError, ValueError):
        return None


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _fts5_available() -> bool:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE probe_fts USING fts5(text)")
        connection.execute("INSERT INTO probe_fts VALUES('evidence lane')")
        return connection.execute("SELECT count(*) FROM probe_fts WHERE probe_fts MATCH 'evidence'").fetchone() == (1,)
    except sqlite3.DatabaseError:
        return False
    finally:
        connection.close()


class RuntimeStatus(Contract):
    observed_at: str
    engine_phase: str
    inventory: dict
    workers: dict
    project: dict | None = None
    client: dict | None = None
    execution_claim: str = "Only recorded operations and job outcomes establish execution."
    native_task_attestation: str = "unavailable"


class RuntimeStatusInput(Contract):
    job_limit: int = Field(default=20, ge=1, le=100)


class CapabilityMonitor:
    def __init__(self, *, requirements: tuple[ToolRequirement, ...] = CORE_TOOLS,
                 optional_runtimes: tuple[OptionalRuntime, ...] = (), device_probe=None, clock=None, installation_status=None):
        if len(requirements) > 256 or len({item.name for item in requirements}) != len(requirements):
            raise ValueError("Register a bounded, unique tool requirement list")
        if len({item.runtime_id for item in optional_runtimes}) != len(optional_runtimes):
            raise ValueError("Select only one environment for each optional runtime")
        self.requirements = requirements
        self.runtimes = {item.runtime_id: item for item in optional_runtimes}
        self.installation_status = installation_status or {'state': 'explicit_engine_configuration',
            'runtime_ids': sorted(self.runtimes), 'full_bundle_ready': False}
        self.device_probe = device_probe or (lambda: probe_nvidia_devices() + probe_amd_devices())
        self.clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._snapshot: dict = {"state": "not_probed", "tools": [], "devices": [], "providers": [], "provider_probes": []}
        self._provider_probes: dict[tuple[str, str], dict] = {}
        self.provider_workers: ProviderWorkers | None = None

    @classmethod
    def from_shared_installation(cls):
        from .installed_providers import load_installed_providers

        runtimes, status = load_installed_providers()
        return cls(optional_runtimes=runtimes, installation_status=status)

    def refresh(self) -> dict:
        tools = []
        for requirement in self.requirements:
            missing_modules = [name for name in requirement.modules if not _module_available(name)]
            missing_commands = [name for name in requirement.commands if shutil.which(name) is None]
            tools.append({"name": requirement.name, "state": "unavailable" if missing_modules or missing_commands else "present",
                          "basis": "module_and_path_lookup", "execution_state": "not_executed",
                          "reason": "DEPENDENCY_NOT_DETECTED" if missing_modules or missing_commands else "DEPENDENCY_PRESENT_EXECUTION_UNTESTED",
                          "missing_modules": missing_modules, "missing_commands": missing_commands})
        fts = _fts5_available()
        tools.append({"name": "sqlite_fts5", "state": "verified" if fts else "unavailable",
                      "basis": "in_memory_insert_and_match", "execution_state": "passed" if fts else "failed",
                      "reason": "FTS5_INSERT_AND_MATCH_PASSED" if fts else "FTS5_INSERT_AND_MATCH_FAILED"})
        providers: list[dict] = []
        for runtime in self.runtimes.values():
            try:
                binding = OptionalRuntime.from_installation(runtime.python.parent.parent, runtime.manifest_path.parent)
                if binding != runtime:
                    raise ValueError("Runtime was reconfigured")
                state, reason = "installed", "LOCKED_INSTALLATION_RECORD_MATCHED"
            except (LaneError, OSError, ValueError, KeyError):
                state, reason = "unavailable", "RUNTIME_BINDING_INVALID"
            providers.append({"runtime_id": runtime.runtime_id, "state": state, "reason": reason,
                              "environment_digest": runtime.lock_sha256, "execution_state": "not_probed_by_inventory"})
        from .tool_catalog import shared_requirements
        for requirement in shared_requirements()['provider_environments']:
            if requirement['runtime_id'] not in self.runtimes:
                providers.append({'runtime_id': requirement['runtime_id'], 'state': 'not_configured',
                    'reason': 'NO_BOUND_PROVIDER_ENVIRONMENT', 'environment_digest': None,
                    'execution_state': 'not_probed_by_inventory'})
        try:
            devices = [item.model_dump(mode="json") for item in self.device_probe()]
            device_state = "observed"
        except (LaneError, OSError, ValueError):
            devices, device_state = [], "unavailable"
        with self._lock:
            self._snapshot = {"state": "observed", "observed_at": self.clock().isoformat(),
                              "tools": tools, "devices": devices, "device_inventory_state": device_state,
                              "device_inventory_observed_at": self.clock().isoformat(),
                              "providers": providers}
        return self.snapshot()

    def probe_provider(self, runtime_id: str, *, device_id: str, device_index: int = 0) -> dict:
        """Trusted engine setup/job caller; user actions must authorize before calling."""
        runtime = self.runtimes.get(runtime_id)
        if runtime is None:
            raise LaneError("OPTIONAL_RUNTIME_NOT_CONFIGURED", "Select an installed provider environment first.")
        result = runtime.probe(device_id=device_id, device_index=device_index, timeout=300)
        with self._lock:
            self._provider_probes[(runtime_id, device_id)] = copy.deepcopy(result)
        return result

    def compute_inventory(self) -> dict:
        """Refresh driver telemetry on demand; never execute a provider self-test.

        Status readers keep using snapshot(). A short cache avoids repeated
        driver processes at consecutive authorization boundaries.
        """
        with self._lock:
            devices = self._snapshot.get('devices', [])
            current = self.clock()
            age = _age(self._snapshot.get('device_inventory_observed_at'), current)
            if age is not None and 0 <= age <= 2 and all(
                    (device_age := _age(row.get('observed_at'), current)) is not None and 0 <= device_age <= 2
                    for row in devices):
                return self.snapshot()
            try:
                devices = [item.model_dump(mode='json') for item in self.device_probe()]
                state = 'observed'
            except (LaneError, OSError, ValueError):
                devices, state = [], 'unavailable'
            self._snapshot.update(devices=devices, device_inventory_state=state,
                                  device_inventory_observed_at=self.clock().isoformat())
            return self.snapshot()

    def snapshot(self) -> dict:
        with self._lock:
            result = copy.deepcopy(self._snapshot)
            current = self.clock()
            result['observation_age_seconds'] = _age(result.get('observed_at'), current)
            result['inventory_scope'] = 'engine_environment; provider_environments_reported_separately'
            result['selection_authorized'] = False
            result['device_freshness'] = []
            for device in result.get('devices', []):
                age = _age(device.get('observed_at'), current)
                result['device_freshness'].append({'device_id': device['device_id'],
                    'observation_age_seconds': age, 'fresh_for_selection': age is not None and 0 <= age <= 10})
            result['provider_installation'] = copy.deepcopy(self.installation_status)
            result['operation_workers'] = self.provider_workers.status() if self.provider_workers is not None else []
            probes = []
            for value in self._provider_probes.values():
                item = copy.deepcopy(value)
                timestamp = item.get("observed_at")
                age = _age(timestamp, current)
                item['observation_age_seconds'] = age
                item["fresh_for_selection"] = age is not None and 0 <= age <= 300 and item["self_test"] == "passed"
                probes.append(item)
            result["provider_probes"] = probes
            return result


def project_health(store, *, limit: int) -> dict:
    """Read existing project state only; missing subsystem tables are visible."""
    with store.connection(read_only=True) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        jobs: dict = {"state": "not_initialized", "counts": {}, "recent": []}
        writer: dict = {"state": "not_initialized", "ownership_basis": "durable_record_only"}
        with store.lane('plan').connection(read_only=True) as plan:
            if plan.execute("SELECT 1 FROM sqlite_schema WHERE name='jobs_jobs'").fetchone():
                jobs = {"state": "available",
                        "counts": {row[0]: row[1] for row in plan.execute("SELECT state,count(*) FROM jobs_jobs GROUP BY state")},
                        "recent": [dict(row) for row in plan.execute(
                            "SELECT job_id,action,state,phase,plan_revision,error_code,updated_at FROM jobs_jobs "
                            "ORDER BY created_at DESC,job_id LIMIT ?", (limit,))]}
        if "writer_lease" in tables:
            row = connection.execute("SELECT fence,owner_id,engine_id,expires_at FROM writer_lease WHERE singleton=1").fetchone()
            writer = {"state": "recorded" if row and row["owner_id"] else "released",
                      "lease": dict(row) if row else None,
                      "ownership_basis": "durable_record_only; kernel ownership must be checked for a write"}
    return {"project_id": store.project_id, "jobs": jobs, "writer": writer}


def runtime_status(engine, context: ActionContext, arguments: RuntimeStatusInput) -> RuntimeStatus:
    from .tool_catalog import snapshot as tool_catalog_snapshot

    project = None
    if context.project_id:
        project = project_health(engine.directory.open(context.project_id), limit=arguments.job_limit)
    own_client = next((item for item in engine.clients.status() if item["client_id"] == context.client_id), None)
    inventory = engine.capabilities.snapshot()
    inventory['tool_catalog'] = tool_catalog_snapshot(inventory)
    inventory['host_observation'] = engine.host_observation.model_dump(mode='json') if engine.host_observation else None
    return RuntimeStatus(observed_at=datetime.now(UTC).isoformat(), engine_phase=engine.phase,
                         inventory=inventory,
                         workers=engine.workers.status() if engine.workers else {"state": "not_configured"},
                         project=project, client=own_client)
