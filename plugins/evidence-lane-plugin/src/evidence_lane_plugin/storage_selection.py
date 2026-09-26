"""Read-only persistence evidence for the selected local project root.

The v4.0.8 host scope supports persistent local Windows Codex Desktop Stable
and Beta. Project registration selects the root. There is no storage connector,
storage-mode mutation, VM durability route, or storage-selection ledger.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .errors import LaneError
from .plan_runtime import content_digest
from .registry import Contract
from .sdk import UUID_PATTERN
from .storage import now, project_snapshot, reject_links

DIGEST = r"^[0-9a-f]{64}$"
StorageClass = Literal["persistent_operator_declared", "unverified"]


class LocalStorageDeclaration(Contract):
    state_root: str = Field(min_length=1, max_length=1024)
    volume_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    storage_class: StorageClass

    @field_validator("state_root")
    @classmethod
    def absolute_root(cls, value):
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts or any(c in value for c in "\r\n\x00"):
            raise ValueError("Declare an absolute state root without traversal")
        return str(path)


class LocalStoragePolicy(Contract):
    format_version: Literal[1] = 1
    declarations: list[LocalStorageDeclaration] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def disjoint_roots(self):
        roots = [Path(row.state_root) for row in self.declarations]
        if any(
            a.is_relative_to(b) or b.is_relative_to(a)
            for index, a in enumerate(roots)
            for b in roots[index + 1 :]
        ):
            raise ValueError("Declare disjoint state roots so each project has one storage policy")
        return self

    @classmethod
    def load(cls, runtime_root):
        path = runtime_root / "storage-policy.json"
        reject_links(path, runtime_root)
        if not path.exists():
            return cls()
        if not path.is_file() or path.stat().st_size > 65_536:
            raise LaneError("STORAGE_POLICY_INVALID", "The owner storage policy exceeds its file budget.")
        try:
            return cls.model_validate_json(path.read_bytes())
        except ValueError:
            raise LaneError("STORAGE_POLICY_INVALID", "The owner storage policy is invalid.") from None


class StorageBackend(Contract):
    project_id: str = Field(pattern=UUID_PATTERN)
    engine_instance_id: str = Field(pattern=UUID_PATTERN)
    connection: Literal["local_api", "remote_api", "unavailable"]
    evidence_basis: str
    storage_class: StorageClass = "unverified"
    volume_id: str | None = None
    policy_digest: str | None = Field(default=None, pattern=DIGEST)
    restart_recovery_verified: bool = False
    restart_verified_at: str | None = None
    observed_at: str
    physical_durability_attested: Literal[False] = False


def local_storage_evidence(engine, project_id):
    project = engine.directory.open(project_id)
    root = project.root.resolve()
    selected = None
    for declaration in engine.local_storage_policy.declarations:
        path = Path(declaration.state_root)
        reject_links(path, Path(path.anchor))
        if root.is_relative_to(path.resolve()):
            selected = declaration
            break
    return StorageBackend(
        project_id=project_id,
        engine_instance_id=engine.instance_id,
        connection="local_api",
        evidence_basis="engine_owner_configuration_at_startup",
        storage_class=selected.storage_class if selected else "unverified",
        volume_id=selected.volume_id if selected else None,
        policy_digest=content_digest(engine.local_storage_policy.model_dump(mode="json")),
        observed_at=now(),
    )


class StorageInspect(Contract):
    pass


class StorageSelectionResult(Contract):
    project_id: str
    backend: StorageBackend
    policy_satisfied: bool
    availability_reason: str
    observation_scope: Literal["current_read"] = "current_read"
    state_authority: Literal["runtime_local_persistence_policy"] = "runtime_local_persistence_policy"
    selection_recorded: Literal[False] = False
    project_migrated: Literal[False] = False
    connection_redirected: Literal[False] = False
    blob_mirror_primary_allowed: Literal[False] = False


class StorageSelection:
    """Compatibility owner for local persistence status and Boot/Resume gating."""

    def __init__(self, engine, project):
        self.engine, self.project = engine, project

    def _backend(self, context):
        if context.storage_observer is None:
            return StorageBackend(
                project_id=self.project.project_id,
                engine_instance_id=self.engine.instance_id,
                connection="unavailable",
                evidence_basis="no_authenticated_backend_observer",
                observed_at=now(),
            )
        backend = StorageBackend.model_validate(context.storage_observer(self.project.project_id))
        if (backend.project_id, backend.engine_instance_id) != (
            self.project.project_id,
            self.engine.instance_id,
        ):
            raise LaneError(
                "STORAGE_BACKEND_BINDING_CHANGED",
                "The persistence observation belongs to another engine or project.",
            )
        return backend

    @staticmethod
    def _availability(backend):
        if backend.connection == "unavailable":
            return False, "authenticated_backend_unavailable"
        if backend.storage_class != "persistent_operator_declared":
            return False, "persistent_storage_not_declared"
        if backend.connection == "remote_api" and not backend.restart_recovery_verified:
            return False, "api_restart_recovery_unverified"
        return True, (
            "persistent_local_operator_declaration"
            if backend.connection == "local_api"
            else "authenticated_api_restart_recovery"
        )

    def inspect(self, context, request):
        with project_snapshot(self.project.root):
            backend = self._backend(context)
            satisfied, reason = self._availability(backend)
            if context.authorize:
                context.authorize("read")
            return StorageSelectionResult(
                project_id=self.project.project_id,
                backend=backend,
                policy_satisfied=satisfied,
                availability_reason=reason,
            )

    def require_current(self, context):
        result = self.inspect(context, StorageInspect())
        if not result.policy_satisfied:
            raise LaneError(
                "STORAGE_ROUTE_UNAVAILABLE",
                "Boot/Resume requires the selected local project root to be declared persistent.",
                details={"reason": result.availability_reason},
            )
        return result
