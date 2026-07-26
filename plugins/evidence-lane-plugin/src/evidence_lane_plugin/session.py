"""Persistent governed session and five-outcome HIL state machine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from .constants import ENGINE_VERSION
from .engine import CodePVEngine
from .errors import EvidenceLaneError, require
from .git_adapter import identity_json, inspect_repository
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes
from .ids import prefixed_id
from .lineage import ChatLineage
from .models import (
    HilDecision,
    HostKind,
    SessionRecord,
    SessionState,
    TaskClass,
    TaskContract,
)
from .pv_package import validate_pv_package
from .redaction import redact
from .store import ProjectStore
from .tasking import classify_task
from .timeutil import utc_now

_TERMINAL_STATES = {
    SessionState.REJECTED_RUN,
    SessionState.FAILED_RUN,
}

_REPOSITORY_IDENTITY_FIELDS = (
    "repository_url",
    "owner",
    "name",
    "commit_sha",
    "tree_sha",
    "worktree_sha256",
)


class SessionManager:
    def __init__(self, store: ProjectStore, engine: CodePVEngine) -> None:
        self.store = store
        self.engine = engine

    def _session_path(self, project_id: str, session_id: str) -> Path:
        root = self.store.project_root(project_id)
        require(
            session_id.startswith("session_") and session_id.replace("_", "").isalnum(),
            "SESSION_ID_INVALID",
            "The session ID is not valid.",
            status="BLOCKED",
        )
        result = (root / "sessions" / f"{session_id}.json").resolve()
        result.relative_to(root)
        return result

    def _lineage_path(self, project_id: str, session_id: str) -> Path:
        return self.store.project_root(project_id) / "lineage" / f"{session_id}.jsonl"

    def _active_path(self, project_id: str) -> Path:
        return self.store.project_root(project_id) / "active_session.json"

    def _verify_repository_matches_package(
        self,
        project_id: str,
        package: Path,
        *,
        error_code: str,
        message: str,
    ) -> dict[str, Any]:
        config = self.store.config(project_id)
        identity = inspect_repository(
            config.repository_path,
            expected_owner=config.expected_owner,
            expected_name=config.expected_name,
        )
        current = identity_json(identity, config.repository_path)
        expected = json.loads(
            (package / "project_identity.json").read_text(encoding="utf-8")
        )["repository"]
        mismatches = {
            field: {"expected": expected.get(field), "current": current.get(field)}
            for field in _REPOSITORY_IDENTITY_FIELDS
            if expected.get(field) != current.get(field)
        }
        require(
            not mismatches,
            error_code,
            message,
            status="MISMATCH",
            mismatches=mismatches,
        )
        return current

    def installation_status(self) -> dict[str, Any]:
        path = self.store.root / "installation.json"
        if not path.is_file():
            return {
                "schema": "evidence-lane.plugin-installation.v1",
                "plugin_id": "evidence-lane-plugin",
                "display_name": "Evidence Lane Plugin",
                "version": ENGINE_VERSION,
                "state": "NOT_INITIALIZED",
                "session_boot_context_inside_pv": False,
                "session_flash_required": True,
                "session_flash_inside_pv": False,
                "hil_approval_inferred": False,
            }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise EvidenceLaneError(
                "PLUGIN_INSTALLATION_RECEIPT_JSON_INVALID",
                "The persistent plugin installation receipt is not valid JSON.",
                status="FAIL",
                details={"error": str(exc)},
            ) from exc
        require(
            payload.get("schema") == "evidence-lane.plugin-installation.v1"
            and payload.get("plugin_id") == "evidence-lane-plugin",
            "PLUGIN_INSTALLATION_RECEIPT_INVALID",
            "The persistent plugin installation receipt is invalid.",
            status="MISMATCH",
        )
        return payload

    def _save(self, session: SessionRecord) -> None:
        session.updated_at = utc_now()
        atomic_write_json(
            self._session_path(session.project_id, session.session_id),
            session.as_dict(),
        )

    @staticmethod
    def _from_payload(payload: dict[str, Any]) -> SessionRecord:
        return SessionRecord(
            session_id=payload["session_id"],
            project_id=payload["project_id"],
            user_id=payload["user_id"],
            workspace_id=payload["workspace_id"],
            host=HostKind(payload["host"]),
            state=SessionState(payload["state"]),
            accepted_pv=payload.get("accepted_pv"),
            accepted_pointer_generation=payload["accepted_pointer_generation"],
            repository=payload["repository"],
            sandbox_id=payload.get("sandbox_id"),
            task=payload.get("task"),
            candidate_id=payload.get("candidate_id"),
            created_at=payload.get("created_at", ""),
            updated_at=payload.get("updated_at", ""),
            metadata=payload.get("metadata", {}),
        )

    def load(self, project_id: str, session_id: str) -> SessionRecord:
        path = self._session_path(project_id, session_id)
        require(
            path.is_file(),
            "SESSION_NOT_FOUND",
            "The governed session does not exist.",
            status="MISMATCH",
            session_id=session_id,
        )
        return self._from_payload(json.loads(path.read_text(encoding="utf-8")))

    def ensure_installation(self) -> dict[str, Any]:
        path = self.store.root / "installation.json"
        if path.exists():
            payload = self.installation_status()
            additions = {
                "version": ENGINE_VERSION,
                "session_boot_context_inside_pv": False,
                "session_flash_required": True,
                "session_flash_inside_pv": False,
                "hil_approval_inferred": False,
            }
            if any(payload.get(key) != value for key, value in additions.items()):
                payload.update(additions)
                atomic_write_json(path, payload)
            return payload
        payload = {
            "schema": "evidence-lane.plugin-installation.v1",
            "plugin_id": "evidence-lane-plugin",
            "display_name": "Evidence Lane Plugin",
            "version": ENGINE_VERSION,
            "state": "INSTALLED_UNTIL_USER_REMOVES_PLUGIN",
            "installed_at": utc_now(),
            "session_boot_context_inside_pv": False,
            "session_flash_required": True,
            "session_flash_inside_pv": False,
            "hil_approval_inferred": False,
        }
        atomic_write_json(path, payload)
        return payload

    def boot(
        self,
        *,
        project_id: str,
        user_id: str,
        workspace_id: str,
        host: HostKind | str,
        agent_id: str,
        sandbox_id: str | None,
        persistence_mode: str,
        ephemeral: bool,
        flash: dict[str, Any],
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        installation = self.ensure_installation()
        config = self.store.config(project_id)
        require(
            config.enabled,
            "PROJECT_DISABLED",
            "The selected project is disabled.",
            status="BLOCKED",
        )
        active_path = self._active_path(project_id)
        if active_path.exists():
            active = json.loads(active_path.read_text(encoding="utf-8"))
            existing = self.load(project_id, active["session_id"])
            require(
                existing.state in _TERMINAL_STATES
                or bool(existing.metadata.get("closed_at")),
                "ONE_SESSION_RULE_ACTIVE",
                "Another governed session is still active for this project.",
                status="BLOCKED",
                active_session_id=existing.session_id,
                state=existing.state.value,
            )
        host_kind = host if isinstance(host, HostKind) else HostKind(host)
        identity = inspect_repository(
            config.repository_path,
            expected_owner=config.expected_owner,
            expected_name=config.expected_name,
        )
        require(
            identity.branch in config.allowed_branches,
            "BRANCH_NOT_AUTHORIZED",
            "The current Git branch is not authorized.",
            status="BLOCKED",
            branch=identity.branch,
        )
        pointer = self.store.pointer(project_id)
        repository_payload = identity_json(identity, config.repository_path)
        if pointer.accepted_pv:
            accepted = self.store.accepted_path(project_id, pointer.accepted_pv)
            validation = validate_pv_package(accepted)
            project_identity = json.loads(
                (accepted / "project_identity.json").read_text(encoding="utf-8")
            )
            prior_repository = project_identity["repository"]
            mismatches = {
                field: {
                    "accepted": prior_repository.get(field),
                    "current": repository_payload.get(field),
                }
                for field in (
                    "repository_url",
                    "owner",
                    "name",
                    "commit_sha",
                    "tree_sha",
                    "worktree_sha256",
                )
                if prior_repository.get(field) != repository_payload.get(field)
            }
            require(
                not mismatches,
                "ACCEPTED_PV_REPOSITORY_MISMATCH",
                "The current repository is not the repository bound to the accepted PV.",
                status="MISMATCH",
                mismatches=mismatches,
            )
            require(
                validation["manifest_sha256"] == pointer.accepted_manifest_sha256,
                "ACCEPTED_POINTER_HASH_MISMATCH",
                "The active pointer does not match the accepted PV manifest.",
                status="MISMATCH",
            )
        session_id = prefixed_id("session")
        now = utc_now()
        safe_context = redact(runtime_context or {})
        context_hash = sha256_bytes(canonical_json_bytes(safe_context))
        session = SessionRecord(
            session_id=session_id,
            project_id=project_id,
            user_id=user_id,
            workspace_id=workspace_id,
            host=host_kind,
            state=SessionState.SESSION_BOOT_FLASH,
            accepted_pv=pointer.accepted_pv,
            accepted_pointer_generation=pointer.generation,
            repository=repository_payload,
            sandbox_id=sandbox_id,
            created_at=now,
            updated_at=now,
            metadata={
                "agent_id": agent_id,
                "turn": 1,
                "runtime_context_sha256": context_hash,
                "runtime_context_stored_in_pv": False,
                "installation_state": installation["state"],
                "flash_authority_version": flash["authority_version"],
                "flash_authority_digest": flash["authority_digest"],
                "flash_action": flash["flash_action"],
                "flash_context_stored_in_pv": False,
                "persistence_mode": persistence_mode,
                "ephemeral_host": ephemeral,
                "source_state": (
                    "ACCEPTED_ENTRY_EXACT"
                    if pointer.accepted_pv is not None
                    else "INITIAL_SOURCE_EXACT"
                ),
                "accepted_pv_query_scope": (
                    "CURRENT_ENTRY_STATE"
                    if pointer.accepted_pv is not None
                    else "NO_ACCEPTED_PV"
                ),
            },
        )
        self._save(session)
        lineage = ChatLineage(self._lineage_path(project_id, session_id))
        lineage.append(
            event_type="session.flash.verified",
            visible_payload={
                "state": SessionState.SESSION_BOOT_FLASH.value,
                "authority_version": flash["authority_version"],
                "authority_digest": flash["authority_digest"],
                "manifest_sha256": flash["manifest_sha256"],
                "flash_action": flash["flash_action"],
                "flash_scope": flash["flash_scope"],
                "environment_operator_data_inside_pv": False,
                "source_packet": flash["source_packet"],
                "warnings": flash["warnings"],
                "hil_approval_inferred": False,
            },
            occurred_at=now,
            session_id=session_id,
        )
        session.state = SessionState.BOOTED
        self._save(session)
        atomic_write_json(
            active_path,
            {"session_id": session_id, "project_id": project_id, "booted_at": now},
        )
        lineage.append(
            event_type="session.boot",
            visible_payload={
                "project_id": project_id,
                "host": host_kind.value,
                "agent_id": agent_id,
                "accepted_pv": pointer.accepted_pv,
                "pointer_generation": pointer.generation,
                "repository": repository_payload,
                "runtime_context_sha256": context_hash,
                "runtime_context_stored_in_pv": False,
                "flash_authority_digest": flash["authority_digest"],
                "flash_context_stored_in_pv": False,
            },
            occurred_at=now,
            session_id=session_id,
        )
        return {
            "status": "PASS",
            "session": session.as_dict(),
            "installation": installation,
            "session_flash": flash,
            "entry_action": (
                "BUILD_PV1_CANDIDATE"
                if pointer.accepted_pv is None
                else "CLASSIFY_ONE_TASK"
            ),
        }

    def build_initial_entry(self, project_id: str, session_id: str) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        require(
            session.state == SessionState.BOOTED and session.accepted_pv is None,
            "INITIAL_ENTRY_STATE_INVALID",
            "PV1 entry build is only valid immediately after a no-PV session boot.",
            status="BLOCKED",
            state=session.state.value,
        )
        run_id = prefixed_id("run")
        lineage = ChatLineage(self._lineage_path(project_id, session_id))
        lineage.append(
            event_type="pv.entry_build.started",
            visible_payload={"run_id": run_id, "proposed_pv": "PV1"},
            occurred_at=utc_now(),
            session_id=session_id,
            run_id=run_id,
        )
        result = self.engine.build_candidate(
            project_id=project_id,
            session=session,
            run_id=run_id,
            lineage_path=self._lineage_path(project_id, session_id),
            task=None,
            initial_entry=True,
        )
        session.candidate_id = result["candidate_id"]
        session.state = SessionState.PV1_CANDIDATE
        session.metadata["run_id"] = run_id
        session.metadata["candidate_pointer_generation"] = (
            session.accepted_pointer_generation
        )
        self._save(session)
        lineage.append(
            event_type="pv.candidate.created",
            visible_payload={
                "candidate_id": result["candidate_id"],
                "proposed_pv": result["proposed_pv"],
                "manifest_sha256": result["manifest_sha256"],
                "warnings": result["warnings"],
            },
            occurred_at=utc_now(),
            session_id=session_id,
            run_id=run_id,
        )
        return {"status": "PASS", "session": session.as_dict(), "candidate": result}

    def classify(
        self,
        project_id: str,
        session_id: str,
        *,
        task_class: str,
        requested_outcome: str,
        permitted_paths: list[str],
        permitted_tools: list[str],
        acceptance_checks: list[str],
        stop_condition: str,
    ) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        pointer = self.store.pointer(project_id)
        pending = session.metadata.get("pending_task")
        pending_state = session.state in {
            SessionState.CORRECTION_TASK_PENDING,
            SessionState.RESEARCH_TASK_PENDING,
        }
        require(
            session.state
            in {
                SessionState.BOOTED,
                SessionState.PVN_ACCEPTED,
                SessionState.PVN1_ACCEPTED,
                SessionState.PVN1_ENTRY,
                SessionState.CORRECTION_TASK_PENDING,
                SessionState.RESEARCH_TASK_PENDING,
            }
            and pointer.accepted_pv is not None,
            "TASK_CLASSIFICATION_STATE_INVALID",
            "A task requires an accepted entry PV and no active task.",
            status="BLOCKED",
            state=session.state.value,
            accepted_pv=pointer.accepted_pv,
        )
        require(
            session.task is None,
            "ONE_TASK_RULE_ACTIVE",
            "The session already has an active task.",
            status="BLOCKED",
        )
        require(
            pointer.generation == session.accepted_pointer_generation,
            "SESSION_POINTER_STALE",
            "The pointer changed after session entry.",
            status="STALE",
        )
        if pending_state:
            require(
                isinstance(pending, dict),
                "PENDING_TASK_CONTRACT_MISSING",
                "The pending HIL outcome has no exact follow-up task contract.",
                status="FAIL",
            )
            exact_pending = cast(dict[str, Any], pending)
            require(
                task_class == exact_pending["required_task_class"],
                "PENDING_TASK_CLASS_MISMATCH",
                "The follow-up task class does not match the exact HIL outcome.",
                status="BLOCKED",
                required=exact_pending["required_task_class"],
            )
            require(
                requested_outcome.strip() == exact_pending["requested_outcome"],
                "PENDING_TASK_OUTCOME_MISMATCH",
                "The follow-up task outcome must exactly match the bounded HIL payload.",
                status="BLOCKED",
                required=exact_pending["requested_outcome"],
            )
            source_candidate_id = cast(str, exact_pending["source_candidate_id"])
            self._verify_repository_matches_package(
                project_id,
                self.store.candidate_path(project_id, source_candidate_id),
                error_code="PENDING_CANDIDATE_SOURCE_MISMATCH",
                message=(
                    "The live source no longer matches the candidate bound to the "
                    "pending HIL task."
                ),
            )
        task = classify_task(
            task_class=task_class,
            requested_outcome=requested_outcome,
            permitted_paths=permitted_paths,
            permitted_tools=permitted_tools,
            acceptance_checks=acceptance_checks,
            stop_condition=stop_condition,
        )
        session.task = task.as_dict()
        session.candidate_id = None
        session.state = (
            SessionState.AWAITING_USER_APPLY_COMMIT
            if session.host == HostKind.CHATGPT
            else SessionState.TASK_CLASSIFIED
        )
        session.metadata["run_id"] = prefixed_id("run")
        session.metadata["source_update_confirmed"] = False
        if pending_state:
            exact_pending = cast(dict[str, Any], pending)
            session.metadata["resumed_from_pending"] = exact_pending
            session.metadata.pop("pending_task", None)
            session.metadata["task_source_basis"] = {
                "kind": "HIL_CANDIDATE_SOURCE",
                "candidate_id": exact_pending["source_candidate_id"],
                "accepted_pv_context": pointer.accepted_pv,
            }
            session.metadata["source_state"] = "PENDING_CANDIDATE_SOURCE_EXACT"
            session.metadata["accepted_pv_query_scope"] = "ENTRY_STATE_ONLY"
        else:
            session.metadata["task_source_basis"] = {
                "kind": "ACCEPTED_PV_ENTRY",
                "accepted_pv": pointer.accepted_pv,
            }
        self._save(session)
        lineage_payload = task.as_dict()
        if pending_state:
            lineage_payload["resumed_from_pending"] = cast(dict[str, Any], pending)[
                "kind"
            ]
        ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="task.classified",
            visible_payload=lineage_payload,
            occurred_at=utc_now(),
            session_id=session_id,
            task_id=task.task_id,
            run_id=session.metadata["run_id"],
        )
        return {"status": "PASS", "session": session.as_dict(), "task": task.as_dict()}

    def record_activity(
        self,
        project_id: str,
        session_id: str,
        *,
        activity_type: str,
        visible_payload: dict[str, Any],
        event_id: str | None = None,
    ) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        require(
            session.task is not None
            and session.state
            in {
                SessionState.TASK_CLASSIFIED,
                SessionState.AWAITING_USER_APPLY_COMMIT,
            },
            "TASK_ACTIVITY_STATE_INVALID",
            "Visible task activity requires one active classified task.",
            status="BLOCKED",
        )
        allowed = {
            "prompt",
            "tool.selected",
            "command.executed",
            "file.inspected",
            "file.created",
            "file.modified",
            "file.deleted",
            "test.output",
            "build.output",
            "git.diff",
            "warning",
            "error",
            "response",
            "usage",
        }
        require(
            activity_type in allowed,
            "ACTIVITY_TYPE_UNSUPPORTED",
            "The activity type is not part of visible operational ChatLineage.",
            status="BLOCKED",
            supported=sorted(allowed),
        )
        task_payload = cast(dict[str, Any], session.task)
        event_payload = dict(visible_payload)
        if activity_type in {"file.created", "file.modified", "file.deleted"}:
            if "first_source_mutation_at" not in session.metadata:
                session.metadata["first_source_mutation_at"] = utc_now()
            session.metadata["source_state"] = "MUTATED_AFTER_ENTRY"
            session.metadata["accepted_pv_query_scope"] = "ENTRY_STATE_ONLY"
            event_payload["source_state_after_activity"] = "MUTATED_AFTER_ENTRY"
            event_payload["accepted_pv_query_scope"] = "ENTRY_STATE_ONLY"
            self._save(session)
        event = ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type=f"task.{activity_type}",
            visible_payload=event_payload,
            occurred_at=utc_now(),
            session_id=session_id,
            task_id=task_payload["task_id"],
            run_id=session.metadata["run_id"],
            event_id=event_id,
        )
        return {
            "status": "PASS",
            "event": event,
            "source_state": session.metadata.get("source_state"),
            "accepted_pv_query_scope": session.metadata.get("accepted_pv_query_scope"),
        }

    def confirm_source_update(
        self,
        project_id: str,
        session_id: str,
        *,
        confirmation: str,
    ) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        require(
            session.task is not None,
            "NO_ACTIVE_TASK",
            "There is no classified task to refresh.",
            status="BLOCKED",
        )
        required = (
            "USER_APPLIED_AND_PULL_CONFIRMED"
            if session.host == HostKind.CHATGPT
            else "HOST_SANDBOX_FINAL_STATE_CONFIRMED"
        )
        require(
            confirmation == required,
            "SOURCE_UPDATE_CONFIRMATION_INVALID",
            "The host-specific source-update confirmation is not exact.",
            status="BLOCKED",
            required=required,
        )
        session.metadata["source_update_confirmed"] = True
        session.metadata["source_update_confirmation"] = confirmation
        self._save(session)
        return {
            "status": "PASS",
            "session_id": session_id,
            "source_update_confirmed": True,
            "confirmation": confirmation,
        }

    def refresh_exit(self, project_id: str, session_id: str) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        require(
            session.task is not None
            and session.state
            in {
                SessionState.TASK_CLASSIFIED,
                SessionState.AWAITING_USER_APPLY_COMMIT,
            },
            "REFRESH_STATE_INVALID",
            "PV Exit requires one active classified task.",
            status="BLOCKED",
            state=session.state.value,
        )
        require(
            bool(session.metadata.get("source_update_confirmed")),
            "SOURCE_UPDATE_NOT_CONFIRMED",
            "The host must explicitly confirm its final source state before Refresh.",
            status="BLOCKED",
        )
        task_payload = cast(dict[str, Any], session.task)
        session.state = SessionState.EXIT_BUILDING
        self._save(session)
        task = TaskContract(
            task_id=task_payload["task_id"],
            task_class=TaskClass(task_payload["task_class"]),
            requested_outcome=task_payload["requested_outcome"],
            permitted_paths=task_payload["permitted_paths"],
            permitted_tools=task_payload["permitted_tools"],
            acceptance_checks=task_payload["acceptance_checks"],
            write_boundary=task_payload["write_boundary"],
            stop_condition=task_payload["stop_condition"],
            hil_required=task_payload["hil_required"],
            status=task_payload["status"],
        )
        result = self.engine.build_candidate(
            project_id=project_id,
            session=session,
            run_id=session.metadata["run_id"],
            lineage_path=self._lineage_path(project_id, session_id),
            task=task,
            initial_entry=False,
        )
        session.candidate_id = result["candidate_id"]
        session.state = SessionState.PVN1_CANDIDATE
        session.metadata["candidate_pointer_generation"] = (
            session.accepted_pointer_generation
        )
        session.metadata["source_state"] = "EXIT_CANDIDATE_BUILT_FROM_FINAL_SOURCE"
        session.metadata["accepted_pv_query_scope"] = "ENTRY_STATE_ONLY_UNTIL_APPROVE"
        self._save(session)
        ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="pv.exit_candidate.created",
            visible_payload={
                "candidate_id": result["candidate_id"],
                "proposed_pv": result["proposed_pv"],
                "manifest_sha256": result["manifest_sha256"],
                "source_delta": result["source_delta"],
                "warnings": result["warnings"],
            },
            occurred_at=utc_now(),
            session_id=session_id,
            task_id=task.task_id,
            run_id=session.metadata["run_id"],
        )
        return {"status": "PASS", "session": session.as_dict(), "candidate": result}

    def decide(
        self,
        project_id: str,
        session_id: str,
        *,
        decision: HilDecision | str,
        decided_by: str,
        reason: str | None = None,
        correction_delta: str | None = None,
        research_question: str | None = None,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        require(
            session.state in {SessionState.PV1_CANDIDATE, SessionState.PVN1_CANDIDATE}
            and session.candidate_id is not None,
            "HIL_DECISION_STATE_INVALID",
            "A HIL decision requires one pending PV candidate.",
            status="BLOCKED",
            state=session.state.value,
        )
        outcome = (
            decision if isinstance(decision, HilDecision) else HilDecision(decision)
        )
        exact_decision_id = decision_id or prefixed_id("decision")
        candidate_id = cast(str, session.candidate_id)
        decision_task_id = session.task["task_id"] if session.task else None
        decision_run_id = session.metadata.get("run_id")
        if outcome == HilDecision.APPROVE:
            require(
                not correction_delta and not research_question,
                "APPROVE_FIELDS_INVALID",
                "APPROVE may not include correction or research payloads.",
                status="BLOCKED",
            )
            result = self.store.promote(
                project_id,
                candidate_id,
                expected_pointer_generation=session.metadata[
                    "candidate_pointer_generation"
                ],
                decided_by=decided_by,
                decision_id=exact_decision_id,
            )
            after = self.store.pointer(project_id)
            session.accepted_pv = after.accepted_pv
            session.accepted_pointer_generation = after.generation
            session.state = (
                SessionState.PVN_ACCEPTED
                if after.accepted_pv == "PV1"
                else SessionState.PVN1_ACCEPTED
            )
            session.metadata["source_state"] = "ACCEPTED_ENTRY_EXACT"
            session.metadata["accepted_pv_query_scope"] = "CURRENT_ENTRY_STATE"
            decision_receipt = result["receipt"]
        else:
            if outcome == HilDecision.APPROVE_WITH_DELTA:
                require(
                    bool(correction_delta and correction_delta.strip()),
                    "CORRECTION_DELTA_REQUIRED",
                    "APPROVE_WITH_DELTA requires one exact bounded correction Delta.",
                    status="BLOCKED",
                )
                target_state = SessionState.CORRECTION_TASK_PENDING
            elif outcome == HilDecision.MORE_RESEARCH:
                require(
                    bool(research_question and research_question.strip()),
                    "RESEARCH_QUESTION_REQUIRED",
                    "MORE_RESEARCH requires one bounded research question.",
                    status="BLOCKED",
                )
                target_state = SessionState.RESEARCH_TASK_PENDING
            elif outcome == HilDecision.REJECT:
                require(
                    bool(reason and reason.strip()),
                    "REJECTION_REASON_REQUIRED",
                    "REJECT requires an exact reason.",
                    status="BLOCKED",
                )
                target_state = SessionState.REJECTED_RUN
            else:
                require(
                    bool(reason and reason.strip()),
                    "FAILURE_REASON_REQUIRED",
                    "FAIL requires the exact failed gate.",
                    status="BLOCKED",
                )
                target_state = SessionState.FAILED_RUN
            decision_receipt = self.store.record_nonpromotion_decision(
                project_id,
                candidate_id=candidate_id,
                decision_id=exact_decision_id,
                decision=outcome.value,
                decided_by=decided_by,
                reason=reason,
                correction_delta=correction_delta,
                research_question=research_question,
            )
            session.state = target_state
            if outcome in {
                HilDecision.APPROVE_WITH_DELTA,
                HilDecision.MORE_RESEARCH,
            }:
                pending_kind = (
                    "CORRECTION"
                    if outcome == HilDecision.APPROVE_WITH_DELTA
                    else "RESEARCH"
                )
                requested_follow_up = (
                    cast(str, correction_delta).strip()
                    if outcome == HilDecision.APPROVE_WITH_DELTA
                    else cast(str, research_question).strip()
                )
                session.metadata.setdefault("completed_runs", []).append(
                    {
                        "run_id": session.metadata.get("run_id"),
                        "task": session.task,
                        "candidate_id": candidate_id,
                        "decision_id": exact_decision_id,
                        "decision": outcome.value,
                    }
                )
                session.metadata["pending_task"] = {
                    "kind": pending_kind,
                    "decision_id": exact_decision_id,
                    "required_task_class": (
                        TaskClass.FIX_BUG.value
                        if outcome == HilDecision.APPROVE_WITH_DELTA
                        else TaskClass.RESEARCH.value
                    ),
                    "requested_outcome": requested_follow_up,
                    "source_candidate_id": candidate_id,
                    "accepted_pv_context": session.accepted_pv,
                    "created_at": utc_now(),
                }
                session.task = None
                session.candidate_id = None
                session.metadata.pop("run_id", None)
                session.metadata["source_update_confirmed"] = False
        session.metadata.setdefault("decisions", []).append(decision_receipt)
        self._save(session)
        ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="hil.decision",
            visible_payload=decision_receipt,
            occurred_at=utc_now(),
            session_id=session_id,
            task_id=decision_task_id,
            run_id=decision_run_id,
        )
        return {
            "status": "PASS",
            "decision": decision_receipt,
            "session": session.as_dict(),
            "pointer": self.store.pointer(project_id).as_dict(),
            "pointer_advanced": outcome == HilDecision.APPROVE,
        }

    def return_to_accepted(
        self,
        project_id: str,
        session_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        require(
            session.state in {SessionState.REJECTED_RUN, SessionState.FAILED_RUN},
            "RETURN_TO_ACCEPTED_STATE_INVALID",
            "Return-to-accepted is valid only after REJECT or FAIL.",
            status="BLOCKED",
            state=session.state.value,
        )
        exact_reason = reason.strip()
        require(
            bool(exact_reason),
            "RETURN_TO_ACCEPTED_REASON_REQUIRED",
            "Return-to-accepted requires a visible reason.",
            status="BLOCKED",
        )
        pointer = self.store.pointer(project_id)
        require(
            pointer.accepted_pv is not None,
            "RETURN_TO_ACCEPTED_NO_ACCEPTED_PV",
            "No accepted PV exists. Close this failed initial-entry session and "
            "restart the initial PV1 flow after correcting the source.",
            status="BLOCKED",
        )
        require(
            pointer.generation == session.accepted_pointer_generation
            and pointer.accepted_pv == session.accepted_pv,
            "RETURN_TO_ACCEPTED_POINTER_STALE",
            "The accepted pointer changed after this session entered.",
            status="STALE",
            pointer=pointer.as_dict(),
            session_accepted_pv=session.accepted_pv,
            session_pointer_generation=session.accepted_pointer_generation,
        )
        self._verify_repository_matches_package(
            project_id,
            self.store.accepted_path(project_id, cast(str, pointer.accepted_pv)),
            error_code="ACCEPTED_SOURCE_RESTORE_REQUIRED",
            message=(
                "Restore the live repository to the exact accepted PV source before "
                "returning this rejected or failed run to accepted state."
            ),
        )
        prior = {
            "state": session.state.value,
            "task": session.task,
            "candidate_id": session.candidate_id,
            "run_id": session.metadata.get("run_id"),
        }
        pointer_digest = sha256_bytes(canonical_json_bytes(pointer.as_dict()))
        session.metadata.setdefault("completed_runs", []).append(
            {
                **prior,
                "return_reason": exact_reason,
                "accepted_pointer_sha256": pointer_digest,
            }
        )
        session.task = None
        session.candidate_id = None
        session.metadata.pop("run_id", None)
        session.metadata["source_update_confirmed"] = False
        session.metadata["source_state"] = "ACCEPTED_ENTRY_EXACT"
        session.metadata["accepted_pv_query_scope"] = "CURRENT_ENTRY_STATE"
        session.metadata["returned_to_accepted"] = {
            "reason": exact_reason,
            "from_state": prior["state"],
            "accepted_pv": pointer.accepted_pv,
            "pointer_generation": pointer.generation,
            "accepted_pointer_sha256": pointer_digest,
            "returned_at": utc_now(),
            "pointer_moved": False,
        }
        session.state = (
            SessionState.PVN_ACCEPTED
            if pointer.accepted_pv == "PV1"
            else SessionState.PVN1_ACCEPTED
        )
        self._save(session)
        event = ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="hil.return_to_accepted",
            visible_payload=session.metadata["returned_to_accepted"],
            occurred_at=utc_now(),
            session_id=session_id,
        )
        return {
            "status": "PASS",
            "session": session.as_dict(),
            "pointer": pointer.as_dict(),
            "pointer_sha256": pointer_digest,
            "pointer_advanced": False,
            "event": event,
        }

    def begin_next_turn(self, project_id: str, session_id: str) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        require(
            session.state in {SessionState.PVN_ACCEPTED, SessionState.PVN1_ACCEPTED},
            "NEXT_TURN_STATE_INVALID",
            "A follow-up turn begins only from a newly accepted PV.",
            status="BLOCKED",
            state=session.state.value,
        )
        pointer = self.store.pointer(project_id)
        session.accepted_pv = pointer.accepted_pv
        session.accepted_pointer_generation = pointer.generation
        session.task = None
        session.candidate_id = None
        session.metadata["turn"] = int(session.metadata.get("turn", 1)) + 1
        session.metadata.pop("run_id", None)
        session.metadata.pop("source_update_confirmed", None)
        session.metadata["next_candidate_would_be"] = self.store.next_pv_id(project_id)
        session.metadata["source_state"] = "ACCEPTED_ENTRY_EXACT"
        session.metadata["accepted_pv_query_scope"] = "CURRENT_ENTRY_STATE"
        session.state = SessionState.PVN1_ENTRY
        self._save(session)
        event = ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="pv.next_entry",
            visible_payload={
                "accepted_entry_pv": pointer.accepted_pv,
                "pointer_generation": pointer.generation,
                "next_candidate_would_be": self.store.next_pv_id(project_id),
            },
            occurred_at=utc_now(),
            session_id=session_id,
        )
        return {
            "status": "PASS",
            "session": session.as_dict(),
            "proof": event["visible_payload"],
        }

    def close(self, project_id: str, session_id: str, *, reason: str) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        require(
            bool(reason.strip()),
            "SESSION_CLOSE_REASON_REQUIRED",
            "Closing a governed session requires a visible reason.",
            status="BLOCKED",
        )
        session.metadata["closed_at"] = utc_now()
        session.metadata["close_reason"] = reason.strip()
        self._save(session)
        active = self._active_path(project_id)
        if active.exists():
            payload = json.loads(active.read_text(encoding="utf-8"))
            if payload.get("session_id") == session_id:
                active.unlink()
        event = ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="session.closed",
            visible_payload={"reason": reason.strip(), "state": session.state.value},
            occurred_at=utc_now(),
            session_id=session_id,
        )
        return {"status": "PASS", "session": session.as_dict(), "event": event}
