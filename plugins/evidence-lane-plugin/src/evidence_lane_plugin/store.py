"""Immutable local project store with compare-and-swap accepted pointers."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, ClassVar, Self, cast

from .constants import POINTER_SCHEMA, PROJECT_REGISTRY_SCHEMA
from .errors import EvidenceLaneError, require
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes
from .models import ActivePointer, ProjectConfig
from .plan_runtime import (
    DELTA_STATUSES,
    append_delta_event,
    append_planning_mode_event,
    ensure_event_ledger,
    plan_runtime_status,
    write_plan_runtime_projection,
)
from .pv_package import compare_package_bytes, validate_pv_package
from .redaction import redact
from .source_authority import snapshot_source_authority_registry
from .tasking import classify_task
from .timeutil import utc_now

_PROJECT_ID_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)

_BATCH_COMPLETION_CONFIRMATION = "BATCH_DELTA_IMPLEMENTATION_EVIDENCE_CONFIRMED"
_BATCH_CONTRACT_FIELDS = (
    "task_id",
    "sequence",
    "plan_id",
    "task_class",
    "requested_outcome",
    "permitted_paths",
    "permitted_tools",
    "acceptance_checks",
    "stop_condition",
)


class _ProjectLock:
    _process_locks: ClassVar[dict[str, threading.RLock]] = {}
    _guard: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def process_lock(cls, path: Path) -> threading.RLock:
        """Return the process-local lock shared by readers and writers."""

        with cls._guard:
            return cls._process_locks.setdefault(str(path), threading.RLock())

    def __init__(self, path: Path, *, timeout: float = 10.0) -> None:
        self.path = path
        self.timeout = timeout
        self._lock = self.process_lock(path)
        self._fd: int | None = None

    def __enter__(self) -> Self:
        self._lock.acquire()
        deadline = time.monotonic() + self.timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self._fd = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    self._lock.release()
                    raise EvidenceLaneError(
                        "PROJECT_STORE_LOCK_TIMEOUT",
                        "The project store remained locked.",
                        status="BLOCKED",
                        details={"lock": str(self.path)},
                    )
                time.sleep(0.05)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if self._fd is not None:
                os.close(self._fd)
            self.path.unlink(missing_ok=True)
        finally:
            self._lock.release()


class ProjectStore:
    def __init__(
        self,
        root: str | Path,
        *,
        configuration_source: str = "EXPLICIT_SERVICE_CONFIGURATION",
    ) -> None:
        raw_root = os.path.expandvars(os.fspath(root)).strip()
        require(
            bool(raw_root),
            "EVIDENCE_LANE_DATA_ROOT_INVALID",
            "The Evidence Lane data root cannot be empty.",
            status="BLOCKED",
        )
        try:
            resolved = Path(raw_root).expanduser().resolve()
            require(
                not resolved.exists() or resolved.is_dir(),
                "EVIDENCE_LANE_DATA_ROOT_UNAVAILABLE",
                "The configured Evidence Lane data root is not a directory.",
                status="BLOCKED",
                resolved_root=str(resolved),
            )
            resolved.mkdir(parents=True, exist_ok=True)
        except EvidenceLaneError:
            raise
        except OSError as exc:
            raise EvidenceLaneError(
                "EVIDENCE_LANE_DATA_ROOT_UNAVAILABLE",
                "The configured Evidence Lane data root could not be opened.",
                status="BLOCKED",
                details={
                    "resolved_root": str(Path(raw_root).expanduser()),
                    "os_error": type(exc).__name__,
                },
            ) from exc
        require(
            resolved.is_absolute() and resolved.is_dir() and os.access(resolved, os.R_OK | os.W_OK),
            "EVIDENCE_LANE_DATA_ROOT_UNAVAILABLE",
            "The configured Evidence Lane data root is not readable and writable.",
            status="BLOCKED",
            resolved_root=str(resolved),
        )
        self.root = resolved
        self.configuration_source = configuration_source

    @staticmethod
    def canonical_project_key(project_id: str) -> str:
        """Return the comparison-only key; the exact ID remains authoritative."""

        return unicodedata.normalize("NFKC", project_id).casefold()

    def _registry_path(self) -> Path:
        return self.root / "registry.json"

    def _registry_lock(self) -> _ProjectLock:
        return _ProjectLock(self.root / ".registry.lock")

    def _load_root_registry(self) -> dict[str, Any]:
        with _ProjectLock.process_lock(self.root / ".registry.lock"):
            return self._load_root_registry_unlocked()

    def _load_root_registry_unlocked(self) -> dict[str, Any]:
        path = self._registry_path()
        if not path.is_file():
            return {"schema": PROJECT_REGISTRY_SCHEMA, "projects": {}}
        try:
            registry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceLaneError(
                "PROJECT_REGISTRY_INVALID",
                "The Evidence Lane root project registry is unreadable.",
                status="FAIL",
                details={"registry": str(path), "error": type(exc).__name__},
            ) from exc
        require(
            registry.get("schema") == PROJECT_REGISTRY_SCHEMA
            and isinstance(registry.get("projects"), dict),
            "PROJECT_REGISTRY_INVALID",
            "The Evidence Lane root project registry has an invalid shape.",
            status="FAIL",
            registry=str(path),
        )
        return registry

    def _assert_exact_project_route(self, project_id: str) -> None:
        registry = self._load_root_registry()
        requested_key = self.canonical_project_key(project_id)
        collisions = sorted(
            existing_id
            for existing_id in registry["projects"]
            if self.canonical_project_key(existing_id) == requested_key
            and existing_id != project_id
        )
        require(
            not collisions,
            "PROJECT_ID_COLLISION",
            "The requested project ID collides by case or Unicode normalization with an existing exact binding.",
            status="BLOCKED",
            project_id=project_id,
            colliding_project_ids=collisions,
            normalization="NFKC_CASEFOLD_COMPARISON_EXACT_ID_AUTHORITY",
        )

    def inspect_root(self) -> dict[str, Any]:
        registry = self._load_root_registry()
        return {
            "schema": "evidence-lane.portable-store-root.v1",
            "status": "PASS",
            "resolved_root": str(self.root),
            "configuration_source": self.configuration_source,
            "root_is_absolute": self.root.is_absolute(),
            "root_exists": self.root.is_dir(),
            "root_readable": os.access(self.root, os.R_OK),
            "root_writable": os.access(self.root, os.W_OK),
            "durability_capability": "CONFIGURED_USER_DURABLE_FILESYSTEM",
            "projects_container": str(self.root / "projects"),
            "registered_project_count": len(registry["projects"]),
            "primary_runtime_storage": "PROJECT_LOCAL_SQLITE_UNLESS_EXPLICITLY_SELECTED_OTHERWISE",
            "google_drive_primary_runtime_allowed": False,
            "secret_values_persisted": False,
        }

    def inspect_project_route(self, project_id: str) -> dict[str, Any]:
        safe = self.validate_project_id(project_id)
        root = self.project_root(safe)
        return {
            "schema": "evidence-lane.project-store-route.v1",
            "status": "PASS",
            "project_id": safe,
            "canonical_comparison_key": self.canonical_project_key(safe),
            "canonical_id_policy": "ASCII_EXACT_WITH_NFKC_CASEFOLD_COLLISION_GUARD",
            "resolved_store_root": str(self.root),
            "relative_project_route": f"projects/{safe}",
            "resolved_project_root": str(root),
            "contained_beneath_store_root": True,
            "transport_project_binding": "EXPLICIT_PROJECT_ID_PER_PROJECT_SCOPED_TOOL",
            "cross_project_fallback_allowed": False,
            "secret_values_persisted": False,
        }

    @staticmethod
    def validate_project_id(project_id: str) -> str:
        require(
            bool(project_id)
            and len(project_id) <= 96
            and all(character in _PROJECT_ID_CHARS for character in project_id)
            and project_id not in {".", ".."}
            and not project_id.startswith("."),
            "PROJECT_ID_INVALID",
            "Project IDs may contain only letters, numbers, dot, underscore, and hyphen.",
            status="BLOCKED",
        )
        return project_id

    def project_root(self, project_id: str) -> Path:
        safe = self.validate_project_id(project_id)
        self._assert_exact_project_route(safe)
        result = (self.root / "projects" / safe).resolve()
        try:
            result.relative_to(self.root)
        except ValueError as exc:
            raise EvidenceLaneError(
                "PROJECT_ROUTE_ESCAPE",
                "The project route escaped the configured Evidence Lane store root.",
                status="BLOCKED",
                details={"project_id": safe},
            ) from exc
        return result

    def _source_authority_path(self, project_id: str) -> Path:
        """Return the project-local registry path without creating it."""

        return self.project_root(project_id) / "source_authority.sqlite"

    def source_authority_path(self, project_id: str) -> Path:
        return self._source_authority_path(project_id)

    def source_authority_status(
        self, project_id: str, *, batch_id: str | None = None
    ) -> dict[str, Any]:
        return snapshot_source_authority_registry(
            self._source_authority_path(project_id), batch_id
        )

    def _lock(self, project_id: str) -> _ProjectLock:
        return _ProjectLock(self.project_root(project_id) / ".store.lock")

    def register_project(self, config: ProjectConfig) -> dict[str, Any]:
        self.validate_project_id(config.project_id)
        with self._registry_lock():
            registry = self._load_root_registry()
            canonical_key = self.canonical_project_key(config.project_id)
            repository_path_hash = sha256_bytes(
                config.repository_path.encode("utf-8")
            )
            id_collisions = sorted(
                existing_id
                for existing_id in registry["projects"]
                if existing_id != config.project_id
                and self.canonical_project_key(existing_id) == canonical_key
            )
            require(
                not id_collisions,
                "PROJECT_ID_COLLISION",
                "The project ID collides by case or Unicode normalization with an existing exact binding.",
                status="BLOCKED",
                project_id=config.project_id,
                colliding_project_ids=id_collisions,
            )
            source_collisions = sorted(
                existing_id
                for existing_id, row in registry["projects"].items()
                if existing_id != config.project_id
                and isinstance(row, dict)
                and row.get("repository_path_hash") == repository_path_hash
            )
            require(
                not source_collisions,
                "PROJECT_SOURCE_BINDING_DUPLICATE",
                "The repository path is already bound to another governed project ID.",
                status="BLOCKED",
                project_id=config.project_id,
                existing_project_ids=source_collisions,
            )
            root = self.project_root(config.project_id)
            with self._lock(config.project_id):
                for folder in (
                    "accepted",
                    "candidates",
                    "receipts",
                    "sessions",
                    "lineage",
                ):
                    (root / folder).mkdir(parents=True, exist_ok=True)
                project_path = root / "project.json"
                payload = {
                    "schema": PROJECT_REGISTRY_SCHEMA,
                    **config.as_dict(),
                }
                if project_path.exists():
                    existing = json.loads(project_path.read_text(encoding="utf-8"))
                    require(
                        existing == payload,
                        "PROJECT_REGISTRATION_CONFLICT",
                        "The project ID is already registered with different authority.",
                        status="MISMATCH",
                        project_id=config.project_id,
                    )
                else:
                    atomic_write_json(project_path, payload)
                pointer_path = root / "active_pointer.json"
                if not pointer_path.exists():
                    pointer = ActivePointer(
                        project_id=config.project_id,
                        accepted_pv=None,
                        accepted_manifest_sha256=None,
                        generation=0,
                        updated_at=utc_now(),
                    )
                    atomic_write_json(
                        pointer_path,
                        {"schema": POINTER_SCHEMA, **pointer.as_dict()},
                    )
                self._update_root_registry(config.project_id, payload, registry)
        return self.project_status(config.project_id)

    def replace_branch_authority(
        self,
        project_id: str,
        *,
        branch: str,
        selected_by: str,
        repository: dict[str, Any],
    ) -> dict[str, Any]:
        """Replace, never broaden, the registered branch with one explicit branch."""

        exact_actor = selected_by.strip()
        require(
            bool(exact_actor),
            "PROJECT_BRANCH_SELECTION_ACTOR_REQUIRED",
            "Replacing branch authority requires the visible governed user.",
            status="BLOCKED",
        )
        project_path = self.project_root(project_id) / "project.json"
        with self._lock(project_id):
            require(
                project_path.is_file(),
                "PROJECT_NOT_REGISTERED",
                "The project is not registered in this Evidence Lane store.",
                status="MISMATCH",
                project_id=project_id,
            )
            existing = json.loads(project_path.read_text(encoding="utf-8"))
            prior_branches = list(existing.get("allowed_branches", []))
            if prior_branches == [branch]:
                return {
                    "status": "UNCHANGED",
                    "project_id": project_id,
                    "prior_allowed_branches": prior_branches,
                    "selected_branch": branch,
                    "authority_broadened": False,
                    "receipt": None,
                }
            updated = {**existing, "allowed_branches": [branch]}
            pointer = self.pointer(project_id)
            receipt_body = {
                "schema": "evidence-lane.branch-authority-selection.v1",
                "project_id": project_id,
                "prior_allowed_branches": prior_branches,
                "selected_branch": branch,
                "selected_by": exact_actor,
                "selected_at": utc_now(),
                "repository": repository,
                "pointer_generation": pointer.generation,
                "accepted_pv": pointer.accepted_pv,
                "authority_broadened": False,
                "remote_write_performed": False,
                "prior_project_sha256": sha256_bytes(canonical_json_bytes(existing)),
                "updated_project_sha256": sha256_bytes(canonical_json_bytes(updated)),
            }
            receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_body))
            receipt = {
                **receipt_body,
                "receipt_id": f"branchauth_{receipt_sha256[:32].lower()}",
                "receipt_sha256": receipt_sha256,
            }
            receipt_path = (
                self.project_root(project_id)
                / "receipts"
                / f"{receipt['receipt_id']}.json"
            )
            atomic_write_json(receipt_path, receipt)
            atomic_write_json(project_path, updated)
        return {
            "status": "REPLACED",
            "project_id": project_id,
            "prior_allowed_branches": prior_branches,
            "selected_branch": branch,
            "authority_broadened": False,
            "receipt": receipt,
        }

    def _update_root_registry(
        self,
        project_id: str,
        project_payload: dict[str, Any],
        registry: dict[str, Any] | None = None,
    ) -> None:
        registry_path = self._registry_path()
        exact_registry = registry or self._load_root_registry()
        exact_registry["projects"][project_id] = {
            "display_name": project_payload["display_name"],
            "enabled": project_payload["enabled"],
            "canonical_project_key": self.canonical_project_key(project_id),
            "relative_project_route": f"projects/{project_id}",
            "repository_path_hash": sha256_bytes(
                project_payload["repository_path"].encode("utf-8")
            ),
        }
        atomic_write_json(registry_path, exact_registry)

    def _backlog_path(self, project_id: str) -> Path:
        return self.project_root(project_id) / "task_backlog.json"

    def _plan_runtime_path(self, project_id: str) -> Path:
        return self.project_root(project_id) / "plan_runtime_projection.sqlite"

    def _persist_backlog(
        self,
        project_id: str,
        backlog: dict[str, Any],
    ) -> None:
        ensure_event_ledger(backlog)
        atomic_write_json(self._backlog_path(project_id), backlog)
        write_plan_runtime_projection(
            self._plan_runtime_path(project_id),
            backlog,
        )

    def _load_backlog(self, project_id: str) -> dict[str, Any]:
        path = self._backlog_path(project_id)
        if not path.is_file():
            return {
                "schema": "evidence-lane.linear-task-backlog.v1",
                "project_id": project_id,
                "plans": [],
                "tasks": [],
            }
        payload = json.loads(path.read_text(encoding="utf-8"))
        require(
            payload.get("schema") == "evidence-lane.linear-task-backlog.v1"
            and payload.get("project_id") == project_id,
            "TASK_BACKLOG_SCHEMA_MISMATCH",
            "The project task backlog is not a supported Evidence Lane authority.",
            status="MISMATCH",
        )
        return payload

    def plan_runtime_status(self, project_id: str) -> dict[str, Any]:
        self.config(project_id)
        backlog = self._load_backlog(project_id)
        return plan_runtime_status(
            self._plan_runtime_path(project_id),
            backlog,
        )

    def plan_tasks(
        self,
        project_id: str,
        *,
        tasks: list[dict[str, Any]],
        planned_by: str,
        plan_id: str,
    ) -> dict[str, Any]:
        """Append a bounded queue; planning never creates parallel active tasks."""
        self.config(project_id)
        require(
            1 <= len(tasks) <= 100,
            "TASK_PLAN_SIZE_INVALID",
            "A linear task plan must contain between one and one hundred tasks.",
            status="BLOCKED",
            count=len(tasks),
        )
        require(
            bool(planned_by.strip()),
            "TASK_PLAN_ACTOR_REQUIRED",
            "Task planning requires the visible human or agent identity.",
            status="BLOCKED",
        )
        require(
            bool(plan_id)
            and len(plan_id) <= 96
            and all(character in _PROJECT_ID_CHARS for character in plan_id),
            "TASK_PLAN_ID_INVALID",
            "The task plan ID is invalid.",
            status="BLOCKED",
        )
        normalized: list[dict[str, Any]] = []
        for position, task in enumerate(tasks, start=1):
            task_id = str(task.get("task_id", ""))
            require(
                bool(task_id)
                and len(task_id) <= 96
                and all(character in _PROJECT_ID_CHARS for character in task_id),
                "BACKLOG_TASK_ID_INVALID",
                "Every queued task requires a stable task ID.",
                status="BLOCKED",
                position=position,
            )
            requested_outcome = str(task.get("requested_outcome", "")).strip()
            stop_condition = str(task.get("stop_condition", "")).strip()
            require(
                bool(requested_outcome) and bool(stop_condition),
                "BACKLOG_TASK_CONTRACT_INCOMPLETE",
                "Every queued task requires an exact outcome and stop condition.",
                status="BLOCKED",
                task_id=task_id,
            )
            arrays: dict[str, list[str]] = {}
            for field, limit in (
                ("permitted_paths", 128),
                ("permitted_tools", 64),
                ("acceptance_checks", 32),
            ):
                values = task.get(field, [])
                require(
                    isinstance(values, list)
                    and len(values) <= limit
                    and all(isinstance(value, str) for value in values),
                    "BACKLOG_TASK_FIELD_INVALID",
                    "A queued task contains an invalid bounded list field.",
                    status="BLOCKED",
                    task_id=task_id,
                    field=field,
                )
                arrays[field] = list(dict.fromkeys(value.strip() for value in values))
            contract = classify_task(
                task_id=task_id,
                task_class=str(task.get("task_class", "")),
                requested_outcome=requested_outcome,
                permitted_paths=arrays["permitted_paths"],
                permitted_tools=arrays["permitted_tools"],
                acceptance_checks=arrays["acceptance_checks"],
                stop_condition=stop_condition,
            )
            normalized_task = {
                key: contract.as_dict()[key]
                for key in (
                    "task_id",
                    "task_class",
                    "requested_outcome",
                    "permitted_paths",
                    "permitted_tools",
                    "acceptance_checks",
                    "stop_condition",
                )
            }
            supersedes_task_id = str(task.get("supersedes_task_id") or "").strip()
            if supersedes_task_id:
                require(
                    supersedes_task_id != task_id
                    and len(supersedes_task_id) <= 96
                    and all(
                        character in _PROJECT_ID_CHARS
                        for character in supersedes_task_id
                    ),
                    "DELTA_SUPERSEDES_TASK_ID_INVALID",
                    "A superseding Delta must name one different stable task ID.",
                    status="BLOCKED",
                    task_id=task_id,
                    supersedes_task_id=supersedes_task_id,
                )
                normalized_task["supersedes_task_id"] = supersedes_task_id
            normalized.append(normalized_task)
        ids = [task["task_id"] for task in normalized]
        require(
            len(ids) == len(set(ids)),
            "BACKLOG_TASK_ID_DUPLICATE",
            "A task plan may not repeat a task ID.",
            status="BLOCKED",
        )
        input_sha256 = sha256_bytes(
            canonical_json_bytes(
                {"planned_by": planned_by.strip(), "tasks": normalized}
            )
        )
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            existing_plan = next(
                (plan for plan in backlog["plans"] if plan["plan_id"] == plan_id),
                None,
            )
            if existing_plan:
                require(
                    existing_plan["input_sha256"] == input_sha256,
                    "TASK_PLAN_ID_CONFLICT",
                    "The task plan ID already binds a different task queue.",
                    status="BLOCKED",
                )
                self._persist_backlog(project_id, backlog)
                return self.backlog_status(project_id)
            existing_ids = {task["task_id"] for task in backlog["tasks"]}
            require(
                not existing_ids.intersection(ids),
                "BACKLOG_TASK_ALREADY_EXISTS",
                "A queued task ID already exists in this project.",
                status="BLOCKED",
                duplicates=sorted(existing_ids.intersection(ids)),
            )
            superseded_ids = {
                str(task.get("supersedes_task_id"))
                for task in normalized
                if task.get("supersedes_task_id")
            }
            missing_superseded = sorted(superseded_ids - existing_ids)
            require(
                not missing_superseded,
                "DELTA_SUPERSEDED_TASK_NOT_FOUND",
                "A superseding Delta references a task outside the existing backlog.",
                status="MISMATCH",
                task_ids=missing_superseded,
            )
            planned_at = utc_now()
            first_sequence = len(backlog["tasks"]) + 1
            added_tasks = [
                {
                    **task,
                    "sequence": first_sequence + offset,
                    "plan_id": plan_id,
                    "status": "QUEUED",
                    "planned_at": planned_at,
                    "history": [],
                }
                for offset, task in enumerate(normalized)
            ]
            backlog["tasks"].extend(added_tasks)
            backlog["plans"].append(
                {
                    "plan_id": plan_id,
                    "planned_by": planned_by.strip(),
                    "input_sha256": input_sha256,
                    "task_ids": ids,
                    "planned_at": planned_at,
                }
            )
            for task in added_tasks:
                append_delta_event(
                    backlog,
                    task_id=task["task_id"],
                    event_type="ADDED",
                    to_status="QUEUED",
                    actor=planned_by.strip(),
                    event_id=f"{plan_id}__{task['task_id']}__added",
                    recorded_at=planned_at,
                    assume_initialized=True,
                    details={
                        "plan_id": plan_id,
                        "sequence": task["sequence"],
                    },
                )
            tasks_by_id = {str(row["task_id"]): row for row in backlog["tasks"]}
            for task in added_tasks:
                linked_task_id = str(task.get("supersedes_task_id") or "")
                if not linked_task_id:
                    continue
                superseded = tasks_by_id[linked_task_id]
                append_delta_event(
                    backlog,
                    task_id=linked_task_id,
                    event_type="SUPERSEDED",
                    to_status="SUPERSEDED",
                    actor=planned_by.strip(),
                    event_id=(
                        f"{plan_id}__{linked_task_id}__superseded_by__{task['task_id']}"
                    ),
                    recorded_at=planned_at,
                    assume_initialized=True,
                    details={"replacement_task_id": task["task_id"]},
                )
                superseded["superseded_by_task_id"] = task["task_id"]
            self._persist_backlog(project_id, backlog)
        return self.backlog_status(project_id)

    def backlog_status(self, project_id: str) -> dict[str, Any]:
        self.config(project_id)
        backlog = self._load_backlog(project_id)
        ensure_event_ledger(backlog)
        for task in backlog["tasks"]:
            task["lifecycle_events"] = [
                event
                for event in backlog["events"]
                if event["task_id"] == task["task_id"]
            ]
        counts = Counter(
            str(task.get("status", "UNKNOWN")) for task in backlog["tasks"]
        )
        active = [task for task in backlog["tasks"] if task.get("status") == "ACTIVE"]
        runtime = plan_runtime_status(
            self._plan_runtime_path(project_id),
            backlog,
        )
        ordered_tasks = sorted(backlog["tasks"], key=lambda row: int(row["sequence"]))
        panel_status = {
            "ACTIVE": "in_progress",
            "DONE": "completed",
            "ACCEPTED": "completed",
        }
        goal_rows = [
            {
                "number": int(task["sequence"]),
                "task_id": str(task["task_id"]),
                "step": str(task["requested_outcome"]),
                "status": panel_status.get(str(task["status"]), "pending"),
                "lifecycle_status": str(task["status"]),
                "steer_deltas": list(task.get("steer_deltas") or []),
            }
            for task in ordered_tasks
        ]
        goal_projection_body = {
            "canonical_authority": "PLAN_LANE",
            "project_id": project_id,
            "task_count": len(goal_rows),
            "rows": goal_rows,
            "persistent_until": "NEXT_SIX_WAY_HIL_PRESENTED",
            "steer_default_boundary": "BEFORE_NEXT_HIL",
            "linked_steer_policy": "APPEND_TO_EXISTING_STEP_WITHOUT_REPLACEMENT",
            "unlinked_steer_policy": "APPEND_NEW_NUMBERED_STEP_AND_INCREASE_COUNT",
        }
        goal_projection = {
            **goal_projection_body,
            "projection_sha256": sha256_bytes(
                canonical_json_bytes(goal_projection_body)
            ),
            "host_projections": {
                "CODEX": {
                    "native_plan_mode": True,
                    "plan_mode_shortcut": "/pl",
                    "plugin_command": "/evi-plan",
                    "native_goal": True,
                    "native_task_panel": True,
                    "goal_start_requires_user_paste": True,
                },
                "CHATGPT": {
                    "native_plan_mode": False,
                    "native_goal": False,
                    "native_task_panel": False,
                    "mounted_plugin_store_is_authority": True,
                    "append_only_lane_law_preserved": True,
                },
            },
            "goal_start_prompt": (
                f"Use the persisted Evidence Lane Plan Lane for project {project_id} "
                "as this Codex task's Goal. Resume the first in-progress or pending "
                "step, keep the full task panel visible through every steer, and stop "
                "at the next governed six-way HIL."
            ),
        }
        return {
            "status": "PASS",
            "schema": backlog["schema"],
            "project_id": project_id,
            "linear_only": True,
            "active_task_limit": 1,
            "counts": dict(sorted(counts.items())),
            "active": active,
            "event_schema": backlog["event_schema"],
            "event_count": len(backlog["events"]),
            "event_head_sha256": backlog.get("event_head_sha256"),
            "planning_mode_event_count": len(backlog["planning_mode_events"]),
            "planning_mode_event_head_sha256": backlog.get(
                "planning_mode_event_head_sha256"
            ),
            "universal_statuses": list(DELTA_STATUSES),
            "plan_runtime_projection": runtime,
            "goal_projection": goal_projection,
            "tasks": backlog["tasks"],
            "plans": backlog["plans"],
        }

    def record_steer_delta(
        self,
        project_id: str,
        *,
        delta_text: str,
        actor: str,
        delta_id: str,
        linked_task_id: str | None = None,
        new_task_contract: dict[str, Any] | None = None,
        boundary: str = "BEFORE_NEXT_HIL",
    ) -> dict[str, Any]:
        """Canonically link a steer or append one new linear Plan Lane row."""

        exact_text = delta_text
        exact_actor = actor.strip()
        exact_delta_id = delta_id.strip()
        exact_boundary = boundary.strip().upper() or "BEFORE_NEXT_HIL"
        exact_link = str(linked_task_id or "").strip()
        require(
            bool(exact_text.strip()) and len(exact_text) <= 50000,
            "STEER_DELTA_TEXT_INVALID",
            "A steer Delta requires its exact visible text.",
            status="BLOCKED",
        )
        require(
            bool(exact_actor),
            "STEER_DELTA_ACTOR_REQUIRED",
            "A steer Delta requires a visible actor.",
            status="BLOCKED",
        )
        require(
            bool(exact_delta_id)
            and len(exact_delta_id) <= 96
            and all(character in _PROJECT_ID_CHARS for character in exact_delta_id),
            "STEER_DELTA_ID_INVALID",
            "A steer Delta requires a stable public-safe ID.",
            status="BLOCKED",
        )
        require(
            bool(exact_boundary) and len(exact_boundary) <= 128,
            "STEER_DELTA_BOUNDARY_INVALID",
            "A steer Delta boundary must be a bounded visible label.",
            status="BLOCKED",
        )
        is_linked = bool(exact_link)
        is_new_step = new_task_contract is not None
        require(
            is_linked != is_new_step,
            "STEER_DELTA_CLASSIFICATION_REQUIRED",
            "Classify the steer as exactly one linked existing step or one new step.",
            status="BLOCKED",
        )

        if is_new_step:
            require(
                isinstance(new_task_contract, dict),
                "STEER_DELTA_NEW_TASK_CONTRACT_INVALID",
                "An unlinked steer requires one complete bounded task contract.",
                status="BLOCKED",
            )
            new_task = cast(dict[str, Any], new_task_contract)
            plan_digest = sha256_bytes(exact_delta_id.encode("utf-8")).lower()
            self.plan_tasks(
                project_id,
                tasks=[new_task],
                planned_by=exact_actor,
                plan_id=f"steerplan_{plan_digest[:32]}",
            )
            exact_link = str(new_task.get("task_id") or "").strip()

        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            task = next(
                (row for row in backlog["tasks"] if row["task_id"] == exact_link),
                None,
            )
            require(
                task is not None,
                "STEER_DELTA_LINKED_TASK_NOT_FOUND",
                "The classified Plan Lane task does not exist.",
                status="MISMATCH",
                task_id=exact_link,
            )
            task = cast(dict[str, Any], task)
            steer_row = {
                "delta_id": exact_delta_id,
                "text": exact_text,
                "boundary": exact_boundary,
                "boundary_defaulted": boundary == "BEFORE_NEXT_HIL",
                "classification": "NEW_STEP" if is_new_step else "LINKED_EXISTING_STEP",
                "linked_task_id": exact_link,
                "recorded_by": exact_actor,
            }
            existing = next(
                (
                    row
                    for candidate in backlog["tasks"]
                    for row in candidate.get("steer_deltas", [])
                    if row.get("delta_id") == exact_delta_id
                ),
                None,
            )
            if existing is not None:
                require(
                    existing == steer_row,
                    "STEER_DELTA_ID_CONFLICT",
                    "The steer Delta ID already binds different immutable content.",
                    status="MISMATCH",
                    delta_id=exact_delta_id,
                )
                self._persist_backlog(project_id, backlog)
                event = None
                idempotent_reuse = True
            else:
                task.setdefault("steer_deltas", []).append(steer_row)
                event_type = (
                    "STEER_DELTA_NEW_STEP" if is_new_step else "STEER_DELTA_LINKED"
                )
                event = append_delta_event(
                    backlog,
                    task_id=exact_link,
                    event_type=event_type,
                    to_status=str(task["status"]),
                    actor=exact_actor,
                    event_id=(
                        "steer_"
                        f"{sha256_bytes(exact_delta_id.encode('utf-8'))[:32].lower()}"
                    ),
                    assume_initialized=True,
                    details={
                        "delta_id": exact_delta_id,
                        "delta_sha256": sha256_bytes(exact_text.encode("utf-8")),
                        "boundary": exact_boundary,
                        "classification": steer_row["classification"],
                        "task_count_changed": is_new_step,
                    },
                )
                self._persist_backlog(project_id, backlog)
                idempotent_reuse = False
        status = self.backlog_status(project_id)
        return {
            "status": "PASS",
            "idempotent_reuse": idempotent_reuse,
            "steer": steer_row,
            "event": event,
            "task_count": status["goal_projection"]["task_count"],
            "task_count_changed": is_new_step,
            "backlog": status,
        }

    def claim_backlog_task(
        self,
        project_id: str,
        *,
        backlog_task_id: str,
        session_id: str,
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            active = [
                task for task in backlog["tasks"] if task.get("status") == "ACTIVE"
            ]
            require(
                not active,
                "BACKLOG_ACTIVE_TASK_EXISTS",
                "The linear backlog already has one active task.",
                status="BLOCKED",
                active_task_ids=[task["task_id"] for task in active],
            )
            task = next(
                (row for row in backlog["tasks"] if row["task_id"] == backlog_task_id),
                None,
            )
            require(
                task is not None and task.get("status") == "QUEUED",
                "BACKLOG_TASK_NOT_QUEUED",
                "The selected backlog task does not exist or is not queued.",
                status="BLOCKED",
                backlog_task_id=backlog_task_id,
            )
            task = cast(dict[str, Any], task)
            exact_fields = (
                "task_class",
                "requested_outcome",
                "permitted_paths",
                "permitted_tools",
                "acceptance_checks",
                "stop_condition",
            )
            mismatches = {
                field: {
                    "planned": task.get(field),
                    "classified": contract.get(field),
                }
                for field in exact_fields
                if task.get(field) != contract.get(field)
            }
            require(
                not mismatches,
                "BACKLOG_TASK_CONTRACT_MISMATCH",
                "Classification must exactly match the queued task contract.",
                status="MISMATCH",
                mismatches=mismatches,
            )
            now = utc_now()
            task["active_session_id"] = session_id
            task["runtime_task_id"] = contract.get("task_id")
            append_delta_event(
                backlog,
                task_id=backlog_task_id,
                event_type="ACTIVATED",
                to_status="ACTIVE",
                actor=session_id,
                event_id=(
                    f"{backlog_task_id}__{session_id}__"
                    f"{contract.get('task_id')}__active"
                ),
                recorded_at=now,
                assume_initialized=True,
                details={
                    "session_id": session_id,
                    "runtime_task_id": contract.get("task_id"),
                },
            )
            task["history"].append(
                {
                    "event": "CLAIMED",
                    "session_id": session_id,
                    "runtime_task_id": contract.get("task_id"),
                    "recorded_at": now,
                }
            )
            self._persist_backlog(project_id, backlog)
            return task

    def record_backlog_done(
        self,
        project_id: str,
        *,
        backlog_task_id: str,
        session_id: str,
        candidate_id: str,
    ) -> dict[str, Any]:
        """Mark one active Delta DONE when automatic Refresh seals its candidate."""

        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            task = next(
                (row for row in backlog["tasks"] if row["task_id"] == backlog_task_id),
                None,
            )
            task = cast(dict[str, Any] | None, task)
            event_id = f"{backlog_task_id}__{candidate_id}__done"
            existing_event = next(
                (event for event in backlog["events"] if event["event_id"] == event_id),
                None,
            )
            if existing_event is not None and task is not None:
                append_delta_event(
                    backlog,
                    task_id=backlog_task_id,
                    event_type="TASK_DONE",
                    to_status="DONE",
                    actor=session_id,
                    event_id=event_id,
                    assume_initialized=True,
                    details={
                        "candidate_id": candidate_id,
                        "session_id": session_id,
                    },
                )
                return task
            require(
                task is not None
                and task.get("status") == "ACTIVE"
                and task.get("active_session_id") == session_id,
                "BACKLOG_TASK_DONE_STATE_INVALID",
                "Only the active task in this session may become DONE.",
                status="BLOCKED",
            )
            task = cast(dict[str, Any], task)
            event = append_delta_event(
                backlog,
                task_id=backlog_task_id,
                event_type="TASK_DONE",
                to_status="DONE",
                actor=session_id,
                event_id=event_id,
                assume_initialized=True,
                details={
                    "candidate_id": candidate_id,
                    "session_id": session_id,
                },
            )
            task["completed_candidate_id"] = candidate_id
            task["history"].append(
                {
                    "event": "TASK_DONE",
                    "candidate_id": candidate_id,
                    "session_id": session_id,
                    "recorded_at": event["recorded_at"],
                }
            )
            self._persist_backlog(project_id, backlog)
            return task

    @staticmethod
    def _validate_batch_completion_evidence(
        backlog: dict[str, Any],
        *,
        task_evidence: list[dict[str, Any]],
        confirmation: str,
    ) -> list[dict[str, Any]]:
        require(
            confirmation == _BATCH_COMPLETION_CONFIRMATION,
            "BATCH_DELTA_COMPLETION_CONFIRMATION_INVALID",
            "Batch completion requires the exact implementation-evidence confirmation.",
            status="BLOCKED",
            required=_BATCH_COMPLETION_CONFIRMATION,
        )
        active = [
            task for task in backlog.get("tasks", []) if task.get("status") == "ACTIVE"
        ]
        require(
            not active,
            "BATCH_DELTA_ACTIVE_TASK_EXISTS",
            "Batch completion cannot bypass an already-active linear Delta.",
            status="BLOCKED",
            active_task_ids=[task.get("task_id") for task in active],
        )
        queued = sorted(
            (
                task
                for task in backlog.get("tasks", [])
                if task.get("status") == "QUEUED"
            ),
            key=lambda task: int(task.get("sequence", 0)),
        )
        require(
            bool(queued),
            "BATCH_DELTA_NO_QUEUED_TASKS",
            "Batch completion requires at least one queued Delta.",
            status="BLOCKED",
        )
        supplied_ids = [str(row.get("task_id", "")) for row in task_evidence]
        expected_ids = [str(task["task_id"]) for task in queued]
        require(
            supplied_ids == expected_ids,
            "BATCH_DELTA_ORDER_OR_SET_MISMATCH",
            "Batch evidence must name every queued Delta exactly once and in ledger order.",
            status="MISMATCH",
            expected_task_ids=expected_ids,
            supplied_task_ids=supplied_ids,
        )
        normalized: list[dict[str, Any]] = []
        for task, raw in zip(queued, task_evidence, strict=True):
            require(
                raw.get("status") == "PASS",
                "BATCH_DELTA_EVIDENCE_NOT_PASS",
                "Every completed Delta requires an explicit PASS evidence status.",
                status="BLOCKED",
                task_id=task["task_id"],
            )
            implementation = raw.get("implementation_evidence")
            verification = raw.get("verification_evidence")
            limitations = raw.get("limitations", [])
            require(
                isinstance(implementation, list)
                and 1 <= len(implementation) <= 64
                and all(
                    isinstance(value, str) and 1 <= len(value.strip()) <= 2048
                    for value in implementation
                )
                and isinstance(verification, list)
                and 1 <= len(verification) <= 64
                and all(
                    isinstance(value, str) and 1 <= len(value.strip()) <= 2048
                    for value in verification
                )
                and isinstance(limitations, list)
                and len(limitations) <= 32
                and all(
                    isinstance(value, str) and 1 <= len(value.strip()) <= 2048
                    for value in limitations
                ),
                "BATCH_DELTA_EVIDENCE_SHAPE_INVALID",
                "Each Delta needs bounded implementation and verification evidence.",
                status="BLOCKED",
                task_id=task["task_id"],
            )
            contract = {field: task.get(field) for field in _BATCH_CONTRACT_FIELDS}
            exact_implementation = cast(list[str], implementation)
            exact_verification = cast(list[str], verification)
            exact_limitations = cast(list[str], limitations)
            safe_evidence = cast(
                dict[str, Any],
                redact(
                    {
                        "task_id": task["task_id"],
                        "status": "PASS",
                        "implementation_evidence": [
                            value.strip() for value in exact_implementation
                        ],
                        "verification_evidence": [
                            value.strip() for value in exact_verification
                        ],
                        "limitations": [value.strip() for value in exact_limitations],
                    }
                ),
            )
            safe_evidence["task_contract_sha256"] = sha256_bytes(
                canonical_json_bytes(contract)
            )
            safe_evidence["evidence_sha256"] = sha256_bytes(
                canonical_json_bytes(safe_evidence)
            )
            normalized.append(safe_evidence)
        return normalized

    def validate_backlog_batch_completion(
        self,
        project_id: str,
        *,
        task_evidence: list[dict[str, Any]],
        confirmation: str,
    ) -> dict[str, Any]:
        self.config(project_id)
        backlog = self._load_backlog(project_id)
        ensure_event_ledger(backlog)
        normalized = self._validate_batch_completion_evidence(
            backlog,
            task_evidence=task_evidence,
            confirmation=confirmation,
        )
        return {
            "status": "PASS",
            "task_count": len(normalized),
            "task_ids": [row["task_id"] for row in normalized],
            "batch_evidence_sha256": sha256_bytes(canonical_json_bytes(normalized)),
            "pointer_mutated": False,
            "candidate_mutated": False,
        }

    def record_backlog_batch_done(
        self,
        project_id: str,
        *,
        session_id: str,
        candidate_id: str,
        task_evidence: list[dict[str, Any]],
        confirmation: str,
    ) -> dict[str, Any]:
        """Atomically append QUEUED -> ACTIVE -> DONE for an exact ordered batch."""

        require(
            bool(session_id.strip()) and bool(candidate_id.strip()),
            "BATCH_DELTA_COMPLETION_BINDING_REQUIRED",
            "Batch completion must bind one governed session and candidate.",
            status="BLOCKED",
        )
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            supplied_ids = [str(row.get("task_id", "")) for row in task_evidence]
            replay = next(
                (
                    row
                    for row in backlog.get("batch_completion_receipts", [])
                    if row.get("session_id") == session_id
                    and row.get("candidate_id") == candidate_id
                    and row.get("task_ids") == supplied_ids
                ),
                None,
            )
            if replay is not None:
                require(
                    confirmation == _BATCH_COMPLETION_CONFIRMATION
                    and len(replay.get("task_evidence", [])) == len(task_evidence),
                    "BATCH_DELTA_REPLAY_INVALID",
                    "A batch replay must use the exact confirmation and task count.",
                    status="MISMATCH",
                )
                for supplied, recorded in zip(
                    task_evidence,
                    replay["task_evidence"],
                    strict=True,
                ):
                    comparable = cast(
                        dict[str, Any],
                        redact(
                            {
                                "task_id": str(supplied.get("task_id", "")),
                                "status": supplied.get("status"),
                                "implementation_evidence": [
                                    str(value).strip()
                                    for value in supplied.get(
                                        "implementation_evidence", []
                                    )
                                ],
                                "verification_evidence": [
                                    str(value).strip()
                                    for value in supplied.get(
                                        "verification_evidence", []
                                    )
                                ],
                                "limitations": [
                                    str(value).strip()
                                    for value in supplied.get("limitations", [])
                                ],
                            }
                        ),
                    )
                    require(
                        all(recorded.get(key) == value for key, value in comparable.items()),
                        "BATCH_DELTA_REPLAY_EVIDENCE_MISMATCH",
                        "The candidate already binds different batch evidence.",
                        status="MISMATCH",
                        task_id=comparable.get("task_id"),
                    )
                return {"status": "PASS", "idempotent": True, **replay}
            normalized = self._validate_batch_completion_evidence(
                backlog,
                task_evidence=task_evidence,
                confirmation=confirmation,
            )
            batch_basis = {
                "project_id": project_id,
                "session_id": session_id,
                "candidate_id": candidate_id,
                "task_evidence": normalized,
            }
            batch_sha256 = sha256_bytes(canonical_json_bytes(batch_basis))
            receipt_id = f"batchdone_{batch_sha256[:32].lower()}"
            receipts = backlog.setdefault("batch_completion_receipts", [])
            existing = next(
                (row for row in receipts if row.get("receipt_id") == receipt_id),
                None,
            )
            if existing is not None:
                require(
                    existing.get("batch_sha256") == batch_sha256,
                    "BATCH_DELTA_RECEIPT_ID_CONFLICT",
                    "The batch receipt ID already binds different evidence.",
                    status="MISMATCH",
                )
                return {"status": "PASS", "idempotent": True, **existing}
            now = utc_now()
            task_rows = {
                str(task["task_id"]): task for task in backlog.get("tasks", [])
            }
            event_ids: list[str] = []
            for position, evidence in enumerate(normalized, start=1):
                task_id = str(evidence["task_id"])
                task = task_rows[task_id]
                active_event = append_delta_event(
                    backlog,
                    task_id=task_id,
                    event_type="ACTIVATED",
                    to_status="ACTIVE",
                    actor=session_id,
                    event_id=f"{receipt_id}__{position:03d}__active",
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        "session_id": session_id,
                        "candidate_id": candidate_id,
                        "batch_receipt_id": receipt_id,
                        "evidence_sha256": evidence["evidence_sha256"],
                    },
                )
                task["active_session_id"] = session_id
                task["history"].append(
                    {
                        "event": "BATCH_CLAIMED",
                        "event_id": active_event["event_id"],
                        "session_id": session_id,
                        "candidate_id": candidate_id,
                        "recorded_at": now,
                    }
                )
                done_event = append_delta_event(
                    backlog,
                    task_id=task_id,
                    event_type="TASK_DONE",
                    to_status="DONE",
                    actor=session_id,
                    event_id=f"{receipt_id}__{position:03d}__done",
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        "session_id": session_id,
                        "candidate_id": candidate_id,
                        "batch_receipt_id": receipt_id,
                        "evidence_sha256": evidence["evidence_sha256"],
                        "task_contract_sha256": evidence[
                            "task_contract_sha256"
                        ],
                    },
                )
                task["completed_candidate_id"] = candidate_id
                task["batch_completion_receipt_id"] = receipt_id
                task["implementation_evidence_sha256"] = evidence[
                    "evidence_sha256"
                ]
                task["history"].append(
                    {
                        "event": "TASK_DONE",
                        "event_id": done_event["event_id"],
                        "candidate_id": candidate_id,
                        "session_id": session_id,
                        "evidence_sha256": evidence["evidence_sha256"],
                        "recorded_at": now,
                    }
                )
                event_ids.extend([active_event["event_id"], done_event["event_id"]])
            receipt = {
                "schema": "evidence-lane.batch-delta-completion.v1",
                "receipt_id": receipt_id,
                "batch_sha256": batch_sha256,
                "project_id": project_id,
                "session_id": session_id,
                "candidate_id": candidate_id,
                "task_count": len(normalized),
                "task_ids": [row["task_id"] for row in normalized],
                "task_evidence": normalized,
                "event_ids": event_ids,
                "completed_at": now,
                "resulting_status": "DONE_PENDING_HIL",
                "pointer_moved": False,
                "candidate_accepted": False,
                "hil_approval_inferred": False,
            }
            receipt["receipt_sha256"] = sha256_bytes(
                canonical_json_bytes(receipt)
            )
            receipts.append(receipt)
            self._persist_backlog(project_id, backlog)
            return {"status": "PASS", "idempotent": False, **receipt}

    def record_backlog_outcome(
        self,
        project_id: str,
        *,
        backlog_task_id: str,
        session_id: str,
        decision: str,
        decided_by: str,
        candidate_id: str,
        accepted_pv: str | None,
    ) -> dict[str, Any]:
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            task = next(
                (row for row in backlog["tasks"] if row["task_id"] == backlog_task_id),
                None,
            )
            task = cast(dict[str, Any] | None, task)
            outcome_status = {
                "APPROVE": "ACCEPTED",
                "APPROVE_WITH_DELTA": "DONE",
                "MORE_RESEARCH": "DONE",
                "ROLLBACK": "ROLLED_BACK",
                "REJECT": "REJECTED",
                "FAIL": "FAILED",
            }[decision]
            event_type = (
                "HIL_FOLLOW_UP_REQUESTED"
                if decision in {"APPROVE_WITH_DELTA", "MORE_RESEARCH"}
                else f"HIL_{decision}"
            )
            outcome_event_id = f"{backlog_task_id}__{candidate_id}__{decision}"
            existing_outcome = next(
                (
                    event
                    for event in backlog["events"]
                    if event["event_id"] == outcome_event_id
                ),
                None,
            )
            if existing_outcome is not None and task is not None:
                append_delta_event(
                    backlog,
                    task_id=backlog_task_id,
                    event_type=event_type,
                    to_status=outcome_status,
                    actor=decided_by,
                    event_id=outcome_event_id,
                    assume_initialized=True,
                    details={
                        "decision": decision,
                        "candidate_id": candidate_id,
                        "accepted_pv": accepted_pv,
                    },
                )
                return task
            require(
                task is not None
                and task.get("status") in {"ACTIVE", "DONE"}
                and task.get("active_session_id") == session_id,
                "BACKLOG_TASK_OUTCOME_STATE_INVALID",
                "Only the active or DONE task in this session may receive a HIL outcome.",
                status="BLOCKED",
            )
            task = cast(dict[str, Any], task)
            if task.get("status") == "ACTIVE":
                append_delta_event(
                    backlog,
                    task_id=backlog_task_id,
                    event_type="TASK_DONE",
                    to_status="DONE",
                    actor=session_id,
                    event_id=f"{backlog_task_id}__{candidate_id}__done",
                    assume_initialized=True,
                    details={
                        "candidate_id": candidate_id,
                        "session_id": session_id,
                        "compatibility_path": "HIL_OUTCOME_AFTER_LEGACY_REFRESH",
                    },
                )
            lifecycle_event = append_delta_event(
                backlog,
                task_id=backlog_task_id,
                event_type=event_type,
                to_status=outcome_status,
                actor=decided_by,
                event_id=outcome_event_id,
                assume_initialized=True,
                details={
                    "decision": decision,
                    "candidate_id": candidate_id,
                    "accepted_pv": accepted_pv,
                },
            )
            task["accepted_pv"] = accepted_pv if decision == "APPROVE" else None
            task["history"].append(
                {
                    "event": "HIL_DECISION",
                    "decision": decision,
                    "candidate_id": candidate_id,
                    "accepted_pv": accepted_pv,
                    "recorded_at": lifecycle_event["recorded_at"],
                }
            )
            self._persist_backlog(project_id, backlog)
            return task

    def record_backlog_batch_outcome(
        self,
        project_id: str,
        *,
        task_ids: list[str],
        session_id: str,
        decision: str,
        decided_by: str,
        candidate_id: str,
        accepted_pv: str | None,
    ) -> dict[str, Any]:
        """Apply one HIL outcome to the exact ordered DONE batch in one write."""

        outcome_status = {
            "APPROVE": "ACCEPTED",
            "APPROVE_WITH_DELTA": "DONE",
            "MORE_RESEARCH": "DONE",
            "ROLLBACK": "ROLLED_BACK",
            "REJECT": "REJECTED",
            "FAIL": "FAILED",
        }.get(decision)
        require(
            outcome_status is not None and bool(task_ids),
            "BATCH_DELTA_HIL_OUTCOME_INVALID",
            "Batch Delta outcome requires one supported HIL decision and task set.",
            status="BLOCKED",
            decision=decision,
        )
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            tasks = {str(row["task_id"]): row for row in backlog.get("tasks", [])}
            require(
                len(task_ids) == len(set(task_ids))
                and all(
                    task_id in tasks
                    and tasks[task_id].get("status") == "DONE"
                    and tasks[task_id].get("completed_candidate_id") == candidate_id
                    for task_id in task_ids
                ),
                "BATCH_DELTA_HIL_TASK_SET_INVALID",
                "The HIL outcome must bind the exact DONE batch for this candidate.",
                status="MISMATCH",
                candidate_id=candidate_id,
            )
            basis = {
                "project_id": project_id,
                "task_ids": task_ids,
                "session_id": session_id,
                "decision": decision,
                "decided_by": decided_by,
                "candidate_id": candidate_id,
                "accepted_pv": accepted_pv,
            }
            basis_sha256 = sha256_bytes(canonical_json_bytes(basis))
            receipt_id = f"batchoutcome_{basis_sha256[:32].lower()}"
            receipts = backlog.setdefault("batch_outcome_receipts", [])
            existing = next(
                (row for row in receipts if row.get("receipt_id") == receipt_id),
                None,
            )
            if existing is not None:
                return {"status": "PASS", "idempotent": True, **existing}
            now = utc_now()
            event_ids: list[str] = []
            for position, task_id in enumerate(task_ids, start=1):
                event = append_delta_event(
                    backlog,
                    task_id=task_id,
                    event_type=(
                        "HIL_FOLLOW_UP_REQUESTED"
                        if outcome_status == "DONE"
                        else "HIL_OUTCOME"
                    ),
                    to_status=cast(str, outcome_status),
                    actor=decided_by,
                    event_id=f"{receipt_id}__{position:03d}",
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        "decision": decision,
                        "candidate_id": candidate_id,
                        "accepted_pv": accepted_pv,
                        "batch_outcome_receipt_id": receipt_id,
                    },
                )
                task = tasks[task_id]
                task["accepted_pv"] = accepted_pv if decision == "APPROVE" else None
                task["history"].append(
                    {
                        "event": "HIL_DECISION",
                        "event_id": event["event_id"],
                        "decision": decision,
                        "candidate_id": candidate_id,
                        "accepted_pv": task["accepted_pv"],
                        "recorded_at": now,
                    }
                )
                event_ids.append(event["event_id"])
            receipt = {
                "schema": "evidence-lane.batch-delta-outcome.v1",
                "receipt_id": receipt_id,
                "basis_sha256": basis_sha256,
                "project_id": project_id,
                "session_id": session_id,
                "candidate_id": candidate_id,
                "task_ids": list(task_ids),
                "task_count": len(task_ids),
                "decision": decision,
                "resulting_status": outcome_status,
                "accepted_pv": accepted_pv if decision == "APPROVE" else None,
                "event_ids": event_ids,
                "recorded_at": now,
            }
            receipt["receipt_sha256"] = sha256_bytes(
                canonical_json_bytes(receipt)
            )
            receipts.append(receipt)
            self._persist_backlog(project_id, backlog)
            return {"status": "PASS", "idempotent": False, **receipt}

    def transition_backlog_task(
        self,
        project_id: str,
        *,
        task_id: str,
        transition_name: str,
        decided_by: str,
        reason_sha256: str,
        replacement_task_id: str | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply one explicit DROP or SUPERSEDE without deleting history."""

        transition = transition_name.strip().upper()
        require(
            transition in {"DROP", "SUPERSEDE"},
            "DELTA_EXPLICIT_TRANSITION_INVALID",
            "Only DROP or SUPERSEDE may be requested directly.",
            status="BLOCKED",
            transition=transition,
        )
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            tasks = {str(row["task_id"]): row for row in backlog["tasks"]}
            require(
                task_id in tasks,
                "DELTA_TASK_NOT_FOUND",
                "The requested Delta does not exist.",
                status="MISMATCH",
                task_id=task_id,
            )
            task = tasks[task_id]
            target_status = "DROPPED" if transition == "DROP" else "SUPERSEDED"
            details: dict[str, Any] = {
                "reason_sha256": reason_sha256,
                "history_preserved": True,
            }
            if transition == "SUPERSEDE":
                exact_replacement = str(replacement_task_id or "").strip()
                require(
                    exact_replacement in tasks
                    and exact_replacement != task_id
                    and tasks[exact_replacement].get("status") == "QUEUED",
                    "DELTA_REPLACEMENT_TASK_INVALID",
                    "SUPERSEDE requires one different queued replacement Delta.",
                    status="BLOCKED",
                    replacement_task_id=exact_replacement or None,
                )
                details["replacement_task_id"] = exact_replacement
                task["superseded_by_task_id"] = exact_replacement
                tasks[exact_replacement]["supersedes_task_id"] = task_id
            lifecycle_event = append_delta_event(
                backlog,
                task_id=task_id,
                event_type=target_status,
                to_status=target_status,
                actor=decided_by,
                event_id=event_id,
                assume_initialized=True,
                details=details,
            )
            if not any(
                row.get("event_id") == lifecycle_event["event_id"]
                for row in task["history"]
            ):
                task["history"].append(
                    {
                        "event": target_status,
                        "event_id": lifecycle_event["event_id"],
                        "recorded_at": lifecycle_event["recorded_at"],
                        **details,
                    }
                )
            self._persist_backlog(project_id, backlog)
            return {
                "status": "PASS",
                "task": task,
                "event": lifecycle_event,
                "backlog": self.backlog_status(project_id),
            }

    def record_planning_mode(
        self,
        project_id: str,
        *,
        source_event_id: str,
        session_id: str,
        request_sha256: str,
        selected_mode_ids: list[str],
        mode_intersection: str,
        canonical_lanes: list[str],
        lifecycle_state: str,
        pointer_generation: int,
    ) -> dict[str, Any]:
        """Append a Planning-mode control-plane event and rebuild its projection."""

        require(
            "PL" in selected_mode_ids and "plan" in canonical_lanes,
            "PLANNING_MODE_PROJECTION_NOT_APPLICABLE",
            "The Plan runtime projection requires a selected Planning mode.",
            status="BLOCKED",
        )
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            event = append_planning_mode_event(
                backlog,
                source_event_id=source_event_id,
                session_id=session_id,
                request_sha256=request_sha256,
                selected_mode_ids=selected_mode_ids,
                mode_intersection=mode_intersection,
                canonical_lanes=canonical_lanes,
                lifecycle_state=lifecycle_state,
                pointer_generation=pointer_generation,
            )
            self._persist_backlog(project_id, backlog)
            return {
                "status": "PASS",
                "append_status": "APPENDED",
                "event": event,
                "projection": plan_runtime_status(
                    self._plan_runtime_path(project_id),
                    backlog,
                ),
                "canonical_plan_sector_mutated": False,
                "projection_role": "DERIVED_CONTROL_PLANE_INDEX",
            }

    def config(self, project_id: str) -> ProjectConfig:
        path = self.project_root(project_id) / "project.json"
        require(
            path.is_file(),
            "PROJECT_NOT_REGISTERED",
            "The project is not registered in this Evidence Lane store.",
            status="MISMATCH",
            project_id=project_id,
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        fields = {
            key: value
            for key, value in payload.items()
            if key in ProjectConfig.__dataclass_fields__
        }
        return ProjectConfig(**fields)

    def pointer(self, project_id: str) -> ActivePointer:
        path = self.project_root(project_id) / "active_pointer.json"
        require(
            path.is_file(),
            "ACTIVE_POINTER_NOT_FOUND",
            "The project has no active pointer.",
            status="MISMATCH",
            project_id=project_id,
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        require(
            payload.get("schema") == POINTER_SCHEMA,
            "ACTIVE_POINTER_SCHEMA_MISMATCH",
            "The project pointer schema is unsupported.",
            status="MISMATCH",
        )
        return ActivePointer(
            **{
                key: value
                for key, value in payload.items()
                if key in ActivePointer.__dataclass_fields__
            }
        )

    def candidate_path(self, project_id: str, candidate_id: str) -> Path:
        require(
            candidate_id.startswith("PV")
            and "_CANDIDATE__RUN_" in candidate_id
            and all(character in _PROJECT_ID_CHARS for character in candidate_id),
            "CANDIDATE_ID_INVALID",
            "The candidate ID does not match the governed naming scheme.",
            status="BLOCKED",
            candidate_id=candidate_id,
        )
        root = self.project_root(project_id)
        target = (root / "candidates" / candidate_id).resolve()
        target.relative_to(root)
        return target

    def accepted_path(self, project_id: str, pv_id: str) -> Path:
        require(
            pv_id.startswith("PV") and pv_id[2:].isdigit() and int(pv_id[2:]) >= 1,
            "PV_ID_INVALID",
            "Accepted PV IDs must use the PV1, PV2, ... sequence.",
            status="BLOCKED",
            pv_id=pv_id,
        )
        root = self.project_root(project_id)
        return (root / "accepted" / pv_id).resolve()

    def next_pv_id(self, project_id: str) -> str:
        accepted = self.accepted_ids(project_id)
        if not accepted:
            return "PV1"
        return f"PV{max(int(pv_id[2:]) for pv_id in accepted) + 1}"

    def accepted_ids(self, project_id: str) -> list[str]:
        root = self.project_root(project_id) / "accepted"
        return sorted(
            (
                path.name
                for path in root.glob("PV*")
                if path.is_dir() and path.name[2:].isdigit() and int(path.name[2:]) >= 1
            ),
            key=lambda value: int(value[2:]),
        )

    def highest_accepted_ordinal(self, project_id: str) -> int:
        accepted = self.accepted_ids(project_id)
        return max((int(value[2:]) for value in accepted), default=0)

    def place_candidate(
        self,
        project_id: str,
        candidate_id: str,
        built_directory: str | Path,
    ) -> dict[str, Any]:
        source = Path(built_directory).resolve()
        validation = validate_pv_package(source)
        require(
            validation["candidate_id"] == candidate_id,
            "CANDIDATE_ID_MISMATCH",
            "The candidate directory and package manifest IDs differ.",
            status="MISMATCH",
        )
        destination = self.candidate_path(project_id, candidate_id)
        with self._lock(project_id):
            if destination.exists():
                comparison = compare_package_bytes(source, destination)
                require(
                    comparison["identical"],
                    "IMMUTABLE_CANDIDATE_CONFLICT",
                    "An immutable candidate already exists with different bytes.",
                    status="BLOCKED",
                    candidate_id=candidate_id,
                )
            else:
                shutil.copytree(source, destination)
                copied = compare_package_bytes(source, destination)
                require(
                    copied["identical"],
                    "CANDIDATE_COPY_MISMATCH",
                    "Candidate bytes changed while entering the immutable store.",
                    status="FAIL",
                )
        return validate_pv_package(destination)

    def promote(
        self,
        project_id: str,
        candidate_id: str,
        *,
        expected_pointer_generation: int,
        decided_by: str,
        decision_id: str,
    ) -> dict[str, Any]:
        candidate = self.candidate_path(project_id, candidate_id)
        candidate_validation = validate_pv_package(candidate)
        exit_slip = json.loads((candidate / "exit_slip.json").read_text(encoding="utf-8"))
        proposed_pv = candidate_validation["proposed_pv"]
        accepted = self.accepted_path(project_id, proposed_pv)
        with self._lock(project_id):
            pointer = self.pointer(project_id)
            require(
                pointer.generation == expected_pointer_generation,
                "POINTER_COMPARE_AND_SWAP_FAILED",
                "The accepted pointer changed after the candidate was created.",
                status="STALE",
                expected_generation=expected_pointer_generation,
                actual_generation=pointer.generation,
            )
            acceptance = exit_slip.get("acceptance_checks") or {}
            pending_postseal = int(
                (acceptance.get("counts") or {}).get("PENDING_POSTSEAL") or 0
            )
            postseal_receipt_validation = None
            if pending_postseal:
                receipt_path = (
                    self.project_root(project_id)
                    / "receipts"
                    / f"postseal_{candidate_id.lower()}.json"
                )
                require(
                    receipt_path.is_file(),
                    "POSTSEAL_ACCEPTANCE_RECEIPT_REQUIRED",
                    "This candidate declares a post-seal acceptance check, but its "
                    "external immutable-candidate receipt is missing.",
                    status="BLOCKED",
                    candidate_id=candidate_id,
                    receipt_path=str(receipt_path),
                )
                try:
                    postseal_receipt = json.loads(
                        receipt_path.read_text(encoding="utf-8")
                    )
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise EvidenceLaneError(
                        "POSTSEAL_ACCEPTANCE_RECEIPT_INVALID",
                        "The external post-seal acceptance receipt is unreadable.",
                        status="FAIL",
                        details={"receipt_path": str(receipt_path)},
                    ) from exc
                claimed_receipt_sha = postseal_receipt.get("receipt_sha256")
                receipt_payload = dict(postseal_receipt)
                receipt_payload.pop("receipt_sha256", None)
                actual_receipt_sha = sha256_bytes(
                    canonical_json_bytes(receipt_payload)
                )
                postseal_health = postseal_receipt.get("acceptance") or {}
                source_commit = (
                    (exit_slip.get("repository_exit") or {}).get("commit_sha")
                )
                require(
                    postseal_receipt.get("schema")
                    == "evidence-lane.postseal-acceptance.receipt.v1"
                    and postseal_receipt.get("project_id") == project_id
                    and postseal_receipt.get("candidate_id") == candidate_id
                    and postseal_receipt.get("accepted_pv_retained")
                    == pointer.accepted_pv
                    and postseal_receipt.get("pointer_generation_retained")
                    == pointer.generation
                    and postseal_receipt.get("source_commit_sha") == source_commit
                    and claimed_receipt_sha == actual_receipt_sha
                    and postseal_health.get("status") == "PASS"
                    and postseal_health.get("verdict")
                    == "ALL_EXECUTABLE_CHECKS_PASS"
                    and postseal_health.get("source_unchanged") is True
                    and int((postseal_health.get("counts") or {}).get("PASS") or 0)
                    == pending_postseal,
                    "POSTSEAL_ACCEPTANCE_RECEIPT_MISMATCH",
                    "The post-seal receipt does not prove every declared post-seal "
                    "check against this exact candidate, source commit, and pointer.",
                    status="FAIL",
                    candidate_id=candidate_id,
                    expected_postseal_checks=pending_postseal,
                    expected_source_commit=source_commit,
                    receipt_path=str(receipt_path),
                )
                postseal_receipt_validation = {
                    "status": "PASS",
                    "path": str(receipt_path),
                    "receipt_sha256": actual_receipt_sha,
                    "checks": pending_postseal,
                }
            require(
                proposed_pv == self.next_pv_id(project_id),
                "PV_SEQUENCE_MISMATCH",
                "The candidate is not the next canonical project version.",
                status="MISMATCH",
                proposed_pv=proposed_pv,
                expected=self.next_pv_id(project_id),
            )
            if accepted.exists():
                comparison = compare_package_bytes(candidate, accepted)
                require(
                    comparison["identical"],
                    "IMMUTABLE_ACCEPTED_PV_CONFLICT",
                    "The accepted PV path already contains different bytes.",
                    status="BLOCKED",
                    proposed_pv=proposed_pv,
                )
            else:
                shutil.copytree(candidate, accepted)
            comparison = compare_package_bytes(candidate, accepted)
            require(
                comparison["identical"],
                "PROMOTION_CHANGED_CANDIDATE_BYTES",
                "Accepted promotion did not preserve candidate bytes.",
                status="FAIL",
            )
            new_pointer = ActivePointer(
                project_id=project_id,
                accepted_pv=proposed_pv,
                accepted_manifest_sha256=candidate_validation["manifest_sha256"],
                generation=pointer.generation + 1,
                prior_generation=pointer.generation,
                updated_at=utc_now(),
            )
            atomic_write_json(
                self.project_root(project_id) / "active_pointer.json",
                {"schema": POINTER_SCHEMA, **new_pointer.as_dict()},
            )
            receipt = {
                "schema": "evidence-lane.pv-promotion.receipt.v1",
                "decision_id": decision_id,
                "decision": "APPROVE",
                "candidate_id": candidate_id,
                "accepted_pv": proposed_pv,
                "manifest_sha256": candidate_validation["manifest_sha256"],
                "candidate_bytes_preserved": True,
                "decided_by": decided_by,
                "pointer_generation_before": pointer.generation,
                "pointer_generation_after": new_pointer.generation,
                "postseal_acceptance": postseal_receipt_validation,
                "decided_at": utc_now(),
            }
            receipt_path = (
                self.project_root(project_id) / "receipts" / f"{decision_id}.json"
            )
            if receipt_path.exists():
                existing = json.loads(receipt_path.read_text(encoding="utf-8"))
                require(
                    existing == receipt,
                    "PROMOTION_RECEIPT_CONFLICT",
                    "The decision ID already has different promotion evidence.",
                    status="BLOCKED",
                )
            else:
                atomic_write_json(receipt_path, receipt)
        return {
            "accepted": validate_pv_package(accepted),
            "pointer": self.pointer(project_id).as_dict(),
            "receipt": receipt,
        }

    def record_nonpromotion_decision(
        self,
        project_id: str,
        *,
        candidate_id: str,
        decision_id: str,
        decision: str,
        decided_by: str,
        reason: str | None,
        correction_delta: str | None,
        research_question: str | None,
    ) -> dict[str, Any]:
        candidate_validation = validate_pv_package(
            self.candidate_path(project_id, candidate_id),
            require_promotable=False,
        )
        pointer = self.pointer(project_id)
        receipt = {
            "schema": "evidence-lane.hil-decision.receipt.v1",
            "decision_id": decision_id,
            "decision": decision,
            "candidate_id": candidate_id,
            "decided_by": decided_by,
            "reason": reason,
            "correction_delta": correction_delta,
            "research_question": research_question,
            "pointer_generation_before": pointer.generation,
            "pointer_generation_after": pointer.generation,
            "accepted_pv_retained": pointer.accepted_pv,
            "candidate_manifest_sha256": candidate_validation["manifest_sha256"],
            "candidate_package_sha256": candidate_validation["package_sha256"],
            "candidate_promotable": candidate_validation["promotable"],
            "candidate_integrity_validated": True,
            "decided_at": utc_now(),
        }
        path = self.project_root(project_id) / "receipts" / f"{decision_id}.json"
        with self._lock(project_id):
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                require(
                    existing == receipt,
                    "HIL_DECISION_ID_CONFLICT",
                    "The HIL decision ID already binds different evidence.",
                    status="BLOCKED",
                )
                return existing
            atomic_write_json(path, receipt)
        return receipt

    def rollback(
        self,
        project_id: str,
        *,
        target_pv: str,
        expected_pointer_generation: int,
        decided_by: str,
        decision_id: str,
        default_entry_target_used: bool,
        entry_pv: str | None,
        candidate_id: str | None,
        freshness: dict[str, Any],
        resolution_reference: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        accepted_index = self.accepted_ids(project_id)
        require(
            target_pv in accepted_index,
            "ROLLBACK_TARGET_NOT_ACCEPTED",
            "Rollback may target only an immutable accepted PV in this project.",
            status="BLOCKED",
            target_pv=target_pv,
            accepted=accepted_index,
        )
        target = self.accepted_path(project_id, target_pv)
        target_validation = validate_pv_package(target)
        with self._lock(project_id):
            before = self.pointer(project_id)
            require(
                before.generation == expected_pointer_generation,
                "ROLLBACK_POINTER_COMPARE_AND_SWAP_FAILED",
                "The accepted pointer changed after the rollback target was verified.",
                status="STALE",
                expected_generation=expected_pointer_generation,
                actual_generation=before.generation,
            )
            pointer_moved = before.accepted_pv != target_pv
            if pointer_moved:
                after = ActivePointer(
                    project_id=project_id,
                    accepted_pv=target_pv,
                    accepted_manifest_sha256=target_validation["manifest_sha256"],
                    generation=before.generation + 1,
                    prior_generation=before.generation,
                    updated_at=utc_now(),
                )
                atomic_write_json(
                    self.project_root(project_id) / "active_pointer.json",
                    {"schema": POINTER_SCHEMA, **after.as_dict()},
                )
            else:
                after = before
            receipt = {
                "schema": "evidence-lane.rollback.receipt.v1",
                "decision_id": decision_id,
                "decision": "ROLLBACK",
                "decided_by": decided_by,
                "requested_target": target_pv,
                "resolved_target": target_pv,
                "default_entry_target_used": default_entry_target_used,
                "session_entry_pv": entry_pv,
                "resolution_reference": resolution_reference,
                "candidate_preserved_unaccepted": candidate_id,
                "pointer_before": before.as_dict(),
                "pointer_after": after.as_dict(),
                "pointer_moved": pointer_moved,
                "target_manifest_sha256": target_validation["manifest_sha256"],
                "target_package_sha256": target_validation["package_sha256"],
                "accepted_ordinal_index": accepted_index,
                "highest_accepted_ordinal": self.highest_accepted_ordinal(project_id),
                "history_preserved": True,
                "live_source_rewritten": False,
                "freshness_after_state_travel": freshness,
                "decided_at": utc_now(),
            }
            path = self.project_root(project_id) / "receipts" / f"{decision_id}.json"
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                require(
                    existing == receipt,
                    "ROLLBACK_DECISION_ID_CONFLICT",
                    "The rollback decision ID already binds different evidence.",
                    status="BLOCKED",
                )
            else:
                atomic_write_json(path, receipt)
        return {
            "status": "PASS",
            "pointer": self.pointer(project_id).as_dict(),
            "receipt": receipt,
            "pointer_moved": pointer_moved,
            "candidate_promoted": False,
        }

    def project_status(self, project_id: str) -> dict[str, Any]:
        root = self.project_root(project_id)
        pointer = self.pointer(project_id)
        accepted = self.accepted_ids(project_id)
        backlog = self._load_backlog(project_id)
        backlog_counts = Counter(
            str(task.get("status", "UNKNOWN")) for task in backlog["tasks"]
        )
        candidates = sorted(
            path.name for path in (root / "candidates").glob("PV*") if path.is_dir()
        )
        return {
            "project": self.config(project_id).as_dict(),
            "pointer": pointer.as_dict(),
            "accepted": accepted,
            "highest_accepted_ordinal": self.highest_accepted_ordinal(project_id),
            "next_candidate_pv": self.next_pv_id(project_id),
            "candidates": candidates,
            "task_backlog": {
                "count": len(backlog["tasks"]),
                "counts": dict(sorted(backlog_counts.items())),
                "active_task_ids": [
                    task["task_id"]
                    for task in backlog["tasks"]
                    if task.get("status") == "ACTIVE"
                ],
            },
        }
