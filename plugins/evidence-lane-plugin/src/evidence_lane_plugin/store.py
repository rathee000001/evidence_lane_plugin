"""Immutable local project store with compare-and-swap accepted pointers."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, ClassVar, Self, cast

from .constants import POINTER_SCHEMA, PROJECT_REGISTRY_SCHEMA
from .errors import EvidenceLaneError, require
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes
from .models import ActivePointer, ProjectConfig, TaskClass
from .pv_package import compare_package_bytes, validate_pv_package
from .timeutil import utc_now

_PROJECT_ID_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


class _ProjectLock:
    _process_locks: ClassVar[dict[str, threading.RLock]] = {}
    _guard: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, path: Path, *, timeout: float = 10.0) -> None:
        self.path = path
        self.timeout = timeout
        with self._guard:
            self._lock = self._process_locks.setdefault(str(path), threading.RLock())
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
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

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
        result = (self.root / "projects" / safe).resolve()
        result.relative_to(self.root)
        return result

    def _lock(self, project_id: str) -> _ProjectLock:
        return _ProjectLock(self.project_root(project_id) / ".store.lock")

    def register_project(self, config: ProjectConfig) -> dict[str, Any]:
        root = self.project_root(config.project_id)
        with self._lock(config.project_id):
            for folder in ("accepted", "candidates", "receipts", "sessions", "lineage"):
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
            self._update_root_registry(config.project_id, payload)
        return self.project_status(config.project_id)

    def _update_root_registry(
        self, project_id: str, project_payload: dict[str, Any]
    ) -> None:
        registry_path = self.root / "registry.json"
        if registry_path.exists():
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        else:
            registry = {"schema": PROJECT_REGISTRY_SCHEMA, "projects": {}}
        registry["projects"][project_id] = {
            "display_name": project_payload["display_name"],
            "enabled": project_payload["enabled"],
            "repository_path_hash": sha256_bytes(
                project_payload["repository_path"].encode("utf-8")
            ),
        }
        atomic_write_json(registry_path, registry)

    def _backlog_path(self, project_id: str) -> Path:
        return self.project_root(project_id) / "task_backlog.json"

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
            try:
                task_class = TaskClass(str(task.get("task_class", ""))).value
            except ValueError as exc:
                raise EvidenceLaneError(
                    "BACKLOG_TASK_CLASS_INVALID",
                    "A queued task has an unsupported task class.",
                    status="BLOCKED",
                    details={"task_id": task_id},
                ) from exc
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
            normalized.append(
                {
                    "task_id": task_id,
                    "task_class": task_class,
                    "requested_outcome": requested_outcome,
                    **arrays,
                    "stop_condition": stop_condition,
                }
            )
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
                return self.backlog_status(project_id)
            existing_ids = {task["task_id"] for task in backlog["tasks"]}
            require(
                not existing_ids.intersection(ids),
                "BACKLOG_TASK_ALREADY_EXISTS",
                "A queued task ID already exists in this project.",
                status="BLOCKED",
                duplicates=sorted(existing_ids.intersection(ids)),
            )
            planned_at = utc_now()
            first_sequence = len(backlog["tasks"]) + 1
            backlog["tasks"].extend(
                {
                    **task,
                    "sequence": first_sequence + offset,
                    "plan_id": plan_id,
                    "status": "QUEUED",
                    "planned_at": planned_at,
                    "history": [],
                }
                for offset, task in enumerate(normalized)
            )
            backlog["plans"].append(
                {
                    "plan_id": plan_id,
                    "planned_by": planned_by.strip(),
                    "input_sha256": input_sha256,
                    "task_ids": ids,
                    "planned_at": planned_at,
                }
            )
            atomic_write_json(self._backlog_path(project_id), backlog)
        return self.backlog_status(project_id)

    def backlog_status(self, project_id: str) -> dict[str, Any]:
        self.config(project_id)
        backlog = self._load_backlog(project_id)
        counts = Counter(
            str(task.get("status", "UNKNOWN")) for task in backlog["tasks"]
        )
        active = [task for task in backlog["tasks"] if task.get("status") == "ACTIVE"]
        return {
            "status": "PASS",
            "schema": backlog["schema"],
            "project_id": project_id,
            "linear_only": True,
            "active_task_limit": 1,
            "counts": dict(sorted(counts.items())),
            "active": active,
            "tasks": backlog["tasks"],
            "plans": backlog["plans"],
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
            task["status"] = "ACTIVE"
            task["active_session_id"] = session_id
            task["runtime_task_id"] = contract.get("task_id")
            task["history"].append(
                {
                    "event": "CLAIMED",
                    "session_id": session_id,
                    "runtime_task_id": contract.get("task_id"),
                    "recorded_at": now,
                }
            )
            atomic_write_json(self._backlog_path(project_id), backlog)
            return task

    def record_backlog_outcome(
        self,
        project_id: str,
        *,
        backlog_task_id: str,
        session_id: str,
        decision: str,
        candidate_id: str,
        accepted_pv: str | None,
    ) -> dict[str, Any]:
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            task = next(
                (row for row in backlog["tasks"] if row["task_id"] == backlog_task_id),
                None,
            )
            require(
                task is not None
                and task.get("status") == "ACTIVE"
                and task.get("active_session_id") == session_id,
                "BACKLOG_TASK_OUTCOME_STATE_INVALID",
                "Only the active task in this session may receive a HIL outcome.",
                status="BLOCKED",
            )
            task = cast(dict[str, Any], task)
            outcome_status = {
                "APPROVE": "COMPLETED_ACCEPTED",
                "APPROVE_WITH_DELTA": "FOLLOW_UP_PENDING",
                "MORE_RESEARCH": "FOLLOW_UP_PENDING",
                "ROLLBACK": "ROLLED_BACK_UNACCEPTED",
                "REJECT": "REJECTED",
                "FAIL": "FAILED",
            }[decision]
            task["status"] = outcome_status
            task["history"].append(
                {
                    "event": "HIL_DECISION",
                    "decision": decision,
                    "candidate_id": candidate_id,
                    "accepted_pv": accepted_pv,
                    "recorded_at": utc_now(),
                }
            )
            atomic_write_json(self._backlog_path(project_id), backlog)
            return task

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
        validate_pv_package(self.candidate_path(project_id, candidate_id))
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
