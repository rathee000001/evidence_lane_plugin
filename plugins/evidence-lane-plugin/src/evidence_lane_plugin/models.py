"""Typed state contracts shared by engine, lifecycle, and MCP tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from .errors import EvidenceLaneError


class HostKind(StrEnum):
    CODEX_DESKTOP = "CODEX_DESKTOP"
    CODEX_CLI = "CODEX_CLI"
    CODEX_VM = "CODEX_VM"
    PUBLIC_AI = "PUBLIC_AI"


_HOST_KIND_ALIASES = {
    "CODEX": HostKind.CODEX_DESKTOP,
    "CODEX_APP": HostKind.CODEX_DESKTOP,
    "CODEX_DESKTOP": HostKind.CODEX_DESKTOP,
    "CODEX_CLI": HostKind.CODEX_CLI,
    "CODEX_VM": HostKind.CODEX_VM,
    "PUBLIC_AI": HostKind.PUBLIC_AI,
}


def normalize_host_kind(value: HostKind | str) -> HostKind:
    """Resolve common host labels into the canonical capability identity."""
    if isinstance(value, HostKind):
        return value
    raw = str(value).strip()
    key = raw.replace("-", "_").replace(" ", "_").upper()
    try:
        return _HOST_KIND_ALIASES[key]
    except KeyError as exc:
        raise EvidenceLaneError(
            "HOST_KIND_INVALID",
            "The host kind is not supported. Use a canonical Codex value or a "
            "documented Codex alias.",
            status="BLOCKED",
            details={
                "provided": raw,
                "supported_values": [item.value for item in HostKind],
                "accepted_aliases": sorted(_HOST_KIND_ALIASES),
            },
        ) from exc


class TaskClass(StrEnum):
    INSPECT = "inspect"
    EXPLAIN = "explain"
    RESEARCH = "research"
    MODIFY_CODE = "modify_code"
    FIX_BUG = "fix_bug"
    ADD_BOUNDED_FEATURE = "add_bounded_feature"
    RUN_TEST = "run_test"
    VERIFY_RESULT = "verify_result"
    PREPARE_PATCH = "prepare_patch"


class HilDecision(StrEnum):
    APPROVE = "APPROVE"
    APPROVE_WITH_DELTA = "APPROVE_WITH_DELTA"
    MORE_RESEARCH = "MORE_RESEARCH"
    ROLLBACK = "ROLLBACK"
    REJECT = "REJECT"
    FAIL = "FAIL"


class SessionState(StrEnum):
    PLUGIN_INSTALLED = "PLUGIN_INSTALLED"
    SESSION_BOOT_FLASH = "SESSION_BOOT_FLASH"
    BOOTED = "BOOTED"
    PV1_CANDIDATE = "PV1_CANDIDATE"
    PVN_ACCEPTED = "PVN_ACCEPTED"
    TASK_CLASSIFIED = "TASK_CLASSIFIED"
    AWAITING_USER_APPLY_COMMIT = "AWAITING_USER_APPLY_COMMIT"
    EXIT_BUILDING = "EXIT_BUILDING"
    PVN1_CANDIDATE = "PVN1_CANDIDATE"
    CORRECTION_TASK_PENDING = "CORRECTION_TASK_PENDING"
    RESEARCH_TASK_PENDING = "RESEARCH_TASK_PENDING"
    REJECTED_RUN = "REJECTED_RUN"
    FAILED_RUN = "FAILED_RUN"
    REMOTE_ACTION_PREPARED = "REMOTE_ACTION_PREPARED"
    REMOTE_ACTION_RECORDED = "REMOTE_ACTION_RECORDED"
    PVN1_ACCEPTED = "PVN1_ACCEPTED"
    PVN1_ENTRY = "PVN1_ENTRY"
    PVN2_WOULD_BE_CANDIDATE = "PVN2_WOULD_BE_CANDIDATE"


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    provider: str
    repository_url: str
    owner: str
    name: str
    branch: str
    commit_sha: str
    tree_sha: str
    is_clean: bool
    submodules: tuple[dict[str, str], ...] = ()
    lfs_state: str = "NOT_USED_OR_UNCHECKED"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EngineIdentity:
    repository: str
    commit: str
    release: str
    package_sha256: str
    schema_version: str
    toolchain_manifest_sha256: str
    signature_key_id: str | None = None
    signature_verified: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProjectConfig:
    project_id: str
    display_name: str
    repository_path: str
    expected_owner: str
    expected_name: str
    allowed_branches: list[str]
    source_lane: str = ""
    persistence_mode: str = "local"
    sensitivity: str = "PUBLIC"
    enabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TaskContract:
    task_id: str
    task_class: TaskClass
    requested_outcome: str
    permitted_paths: list[str]
    permitted_tools: list[str]
    acceptance_checks: list[str]
    write_boundary: str
    stop_condition: str
    hil_required: bool
    status: str = "CLASSIFIED"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["task_class"] = self.task_class.value
        return payload


@dataclass(slots=True)
class ActivePointer:
    project_id: str
    accepted_pv: str | None
    accepted_manifest_sha256: str | None
    generation: int
    updated_at: str
    prior_generation: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    project_id: str
    user_id: str
    workspace_id: str
    host: HostKind
    state: SessionState
    accepted_pv: str | None
    accepted_pointer_generation: int
    repository: dict[str, Any]
    sandbox_id: str | None = None
    task: dict[str, Any] | None = None
    candidate_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["host"] = self.host.value
        payload["state"] = self.state.value
        return payload
