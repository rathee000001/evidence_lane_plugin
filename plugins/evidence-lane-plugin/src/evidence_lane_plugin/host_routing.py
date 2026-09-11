"""Fresh, attributed host observations and the retained Codex host routing matrix.

Derived from v3 codex_action_plane/runtime_host_classifier. Expected host features
are routing policy, not observations. Client initialization cannot attest task identity.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator

from .errors import LaneError
from .registry import Contract

HOST_MATRIX = {
    "codex_desktop_stable": {"family": "codex_desktop", "channel": "stable", "storage_policy": "selected_local_or_remote"},
    "codex_desktop_beta": {"family": "codex_desktop", "channel": "beta", "storage_policy": "selected_local_or_remote"},
    "codex_desktop": {"family": "codex_desktop", "channel": "unverified", "storage_policy": "selected_local_or_remote"},
    "codex_cli": {"family": "codex_cli", "channel": "cli", "storage_policy": "selected_local_or_remote"},
    "codex_vm_persistent": {"family": "codex_vm", "channel": "persistent", "storage_policy": "durability_verification_required"},
    "codex_vm_ephemeral": {"family": "codex_vm", "channel": "ephemeral", "storage_policy": "durable_remote_required"},
    "unknown": {"family": "unknown", "channel": "unverified", "storage_policy": "explicit_selection_required"},
}


class ClientHello(Contract):
    configured_profile: str = "unknown"
    protocol: Literal["local_api", "mcp_stdio"] = "local_api"
    peer_name: str | None = Field(default=None, max_length=128)
    peer_version: str | None = Field(default=None, max_length=128)
    protocol_version: str | None = Field(default=None, max_length=32)
    peer_capabilities: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("configured_profile")
    @classmethod
    def supported_profile(cls, value):
        if value not in HOST_MATRIX:
            raise ValueError("Unknown configured host profile")
        return value

    @field_validator("peer_capabilities")
    @classmethod
    def capability_names(cls, value):
        if not set(value) <= {"roots", "sampling", "elicitation", "tasks", "experimental"}:
            raise ValueError("Only negotiated MCP capability names may be reported")
        return sorted(set(value))


class HostObservation(Contract):
    observation_id: str
    observed_at: str
    trigger: Literal["engine_start", "client_connect"]
    operating_system: str
    architecture: str
    python_version: str
    cpu_count: int
    available_commands: list[str]
    local_measurement_basis: Literal["engine_os_and_path_probe"] = "engine_os_and_path_probe"
    client: ClientHello
    client_evidence_basis: Literal["authenticated_client_report"] = "authenticated_client_report"
    local_transport: Literal["windows_current_user_loopback", "posix_current_user_loopback", "unavailable"]
    configured_storage_policy: str
    host_profile_attestation: Literal["unavailable"] = "unavailable"
    native_task_attestation: Literal["unavailable"] = "unavailable"
    native_hooks: Literal["unavailable"] = "unavailable"
    native_goal: Literal["unavailable"] = "unavailable"
    native_plan: Literal["unavailable"] = "unavailable"
    model_identity: Literal["unavailable"] = "unavailable"
    engine_studio_platform_supported: bool = False
    studio_installation: Literal['not_measured'] = 'not_measured'
    managed_toolchain_readiness: Literal['requires_windows_studio_probe', 'not_supported_on_this_platform'] = 'not_supported_on_this_platform'


class HostDetector:
    """No process-name, title, environment-token or package-name identity inference."""

    def __init__(self, *, system=None, machine=None, cpu_count=None, which=None, clock=None):
        self.system = system or platform.system
        self.machine = machine or platform.machine
        self.cpu_count = cpu_count or os.cpu_count
        self.which = which or shutil.which
        self.clock = clock or (lambda: datetime.now(UTC))

    def inspect(self, *, trigger: Literal["engine_start", "client_connect"],
                client: ClientHello | None = None) -> HostObservation:
        hello = ClientHello.model_validate((client or ClientHello()).model_dump())
        system = self.system()
        # Presence is useful for route diagnostics; successful execution is measured later.
        commands = [name for name in ("git", "node", "java", "go", "rustc", "tesseract", "pdftoppm")
                    if self.which(name) is not None]
        return HostObservation(
            observation_id=str(uuid4()), observed_at=self.clock().isoformat(), trigger=trigger,
            operating_system=system, architecture=self.machine(),
            python_version=".".join(str(item) for item in sys.version_info[:3]),
            cpu_count=self.cpu_count() or 1, available_commands=commands, client=hello,
            local_transport=("windows_current_user_loopback" if system == "Windows" else
                             "posix_current_user_loopback" if system in {'Darwin', 'Linux'} else 'unavailable'),
            configured_storage_policy=HOST_MATRIX[hello.configured_profile]["storage_policy"],
            engine_studio_platform_supported=system == 'Windows',
            managed_toolchain_readiness='requires_windows_studio_probe' if system == 'Windows' else 'not_supported_on_this_platform',
        )


def select_host_route(observation: HostObservation, *, remote_ready: bool = False,
                      durable_remote_verified: bool = False, exact_task_required: bool = False,
                      prefer_remote: bool = False) -> dict:
    """Read-only route planning using current probes; it grants no action permissions."""
    if exact_task_required:
        raise LaneError("NATIVE_TASK_ATTESTATION_UNAVAILABLE", "The host has not provided a verified native task binding.")
    profile = observation.client.configured_profile
    if prefer_remote or profile in {"codex_vm_ephemeral", "codex_vm_persistent"}:
        if not durable_remote_verified:
            return {"route": None, "reason": "durable_storage_verification_required", "execution_authorized": False}
        if remote_ready:
            return {"route": "remote_api", "reason": "verified_remote_route", "execution_authorized": False}
        return {"route": None, "reason": "remote_api_unavailable", "execution_authorized": False}
    if observation.local_transport == "windows_current_user_loopback":
        return {"route": "local_loopback", "reason": "measured_windows_transport", "execution_authorized": False}
    if observation.local_transport == 'posix_current_user_loopback':
        return {'route': 'local_loopback', 'reason': 'reduced_current_user_transport', 'execution_authorized': False,
                'studio_supported': False, 'managed_windows_toolchain': False}
    if remote_ready and durable_remote_verified:
        return {"route": "remote_api", "reason": "verified_remote_route", "execution_authorized": False}
    return {"route": None, "reason": "local_transport_unavailable_remote_required", "execution_authorized": False}
