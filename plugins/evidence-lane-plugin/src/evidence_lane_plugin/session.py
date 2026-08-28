"""Persistent governed session and explicit six-outcome HIL state machine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from .canon_runtime_continuity import seal_observed_experience_packet
from .capture_routing import CaptureRouteAuthority
from .constants import (
    ENGINE_VERSION,
    GOVERNED_SKILL_COUNT,
    NATIVE_READ_TOOL_COUNT,
    NATIVE_TOOL_COUNT,
    NATIVE_WRITE_TOOL_COUNT,
)
from .engine import CodePVEngine
from .errors import EvidenceLaneError, require
from .freshness import evaluate_freshness, evaluate_working_lane_freshness
from .git_adapter import (
    calculate_worktree_sha256,
    identity_json,
    inspect_repository,
    run_git,
    run_git_digest,
)
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes, sha256_file
from .host_plan_rehydration import prepare_host_plan_rehydration
from .ids import prefixed_id
from .ingest import iter_source_files
from .install_deferral import adaptive_install_deferral_facts
from .lanes import LaneRegistryError, resolve_lane_id
from .lineage import ChatLineage
from .mcp_apps import PROJECT_PANEL_SCHEMA
from .mode_governance import validate_mode_governance_selection
from .models import (
    ActivePointer,
    HilDecision,
    HostKind,
    SessionRecord,
    SessionState,
    TaskClass,
    TaskContract,
    normalize_host_kind,
)
from .next_actions import (
    SOURCE_INTAKE_COMMANDS,
    boot_next_action,
)
from .package_root import resolve_plugin_root
from .project_authority import resolved_chat_lineage_root, resolved_plan_auxiliary_path
from .prompt_index import PromptIndex, is_prompt_reference
from .pv_package import validate_pv_package
from .redaction import redact
from .runtime_activation import RuntimeActivation
from .runtime_continuity import (
    build_runtime_continuity,
    validate_runtime_continuity,
)
from .state_law import LifecycleEvent, transition
from .state_travel_contract import (
    build_direct_destination_orchestration,
    execution_profile_from_context,
    execution_profile_mismatches,
    normalize_direct_forced_same_worktree_binding,
)
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

_PLAN_NORMALIZATION_SCHEMA = "evidence-lane.plan-normalization-transaction.v1"
_PLAN_NORMALIZATION_PHASES = {
    "PREPARED",
    "PLAN_APPENDED",
    "PLAN_ACTIVATED",
    "PLAN_CORRECTED",
    "SESSION_REBOUND",
    "COMMITTED",
}
_ACTIVE_CONTRACT_REBIND_SCHEMA = "evidence-lane.active-contract-rebind-transaction.v1"
_ACTIVE_CONTRACT_REBIND_PHASES = {
    "PREPARED",
    "PLAN_AMENDED",
    "SESSION_REBOUND",
    "COMMITTED",
}
_SAFE_ID_CHARACTERS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_EXACT_TASK_PROJECT_SESSION_BINDING_TASK_ID = (
    "EL-CODEX-EXACT_TASK_PROJECT_SESSION_BINDING-PROPOSAL-03"
)
_STATE_TRAVEL_CONSUMED_STATUSES = {
    "VERIFIED_WAITING",
    "VERIFIED_RESUME_READY",
}


def _require_sha256(value: Any, *, field: str) -> str:
    exact = str(value or "").strip().upper()
    require(
        len(exact) == 64
        and all(character in "0123456789ABCDEF" for character in exact),
        "PLAN_NORMALIZATION_SHA256_INVALID",
        "Every Plan-normalization precondition hash must be one exact SHA-256.",
        status="BLOCKED",
        field=field,
    )
    return exact


def _require_active_contract_sha256(value: Any, *, field: str) -> str:
    exact = str(value or "").strip().upper()
    require(
        len(exact) == 64
        and all(character in "0123456789ABCDEF" for character in exact),
        "ACTIVE_CONTRACT_REBIND_SHA256_INVALID",
        "Every active-contract rebind authority must be one exact SHA-256.",
        status="BLOCKED",
        field=field,
    )
    return exact


def _normalize_pv_target(value: str) -> str:
    normalized = value.strip().upper()
    if normalized.replace(".", "").isdigit() and not normalized.startswith("PV"):
        normalized = f"PV{normalized}"
    ordinal_parts = normalized[2:].split(".") if normalized.startswith("PV") else []
    require(
        len(ordinal_parts) in {1, 3}
        and all(part.isdigit() for part in ordinal_parts)
        and int(ordinal_parts[0]) >= 1
        and all(int(part) >= 0 for part in ordinal_parts[1:]),
        "ROLLBACK_TARGET_INVALID",
        "Rollback target must be a full PV such as PV2/2 or an accepted sub-PV "
        "such as PV2.3.1.",
        status="BLOCKED",
        target=value,
    )
    return normalized


class SessionManager:
    def __init__(
        self,
        store: ProjectStore,
        engine: CodePVEngine,
        *,
        runtime_activation: RuntimeActivation,
    ) -> None:
        self.store = store
        self.engine = engine
        self.runtime_activation = runtime_activation
        runtime_body = {
            "schema": "evidence-lane.server-runtime-instance-attestation.v1",
            "status": "PASS",
            "runtime_instance_id": prefixed_id("runtime"),
            "identity_source": "SERVER_INTERNAL_PROCESS_LIFETIME",
            "caller_supplied": False,
            "process_id_exposed": False,
            "attested_at": utc_now(),
        }
        self._runtime_instance_attestation = {
            **runtime_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(runtime_body)),
        }

    def _current_live_root_freshness(
        self,
        project_id: str,
        *,
        bounded_dirty_read: bool = False,
    ) -> dict[str, Any]:
        """Compare current source with live authority, never accepted history."""

        pointer = self.store.pointer(project_id)
        if pointer.accepted_pv is None:
            return {
                "state": "PENDING_INITIAL_CANDIDATE",
                "reason": (
                    "No accepted pointer exists; the current live root remains "
                    "the only source authority."
                ),
                "authority": "LIVE_PROJECT_ROOT",
            }
        if self.store.uses_external_project_authority(project_id):
            sectors = self.store.project_root(project_id) / "sectors"
            require(
                (sectors / "manifest.json").is_file(),
                "LIVE_ROOT_SECTOR_MANIFEST_MISSING",
                "External project freshness requires the current live sector manifest.",
                status="MISMATCH",
                project_id=project_id,
            )
            return evaluate_working_lane_freshness(
                self.store,
                project_id,
                sectors,
                bounded_dirty_read=bounded_dirty_read,
            )
        return evaluate_freshness(
            self.store,
            project_id,
            self.store.accepted_path(project_id, pointer.accepted_pv),
            bounded_dirty_read=bounded_dirty_read,
        )

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
        return (
            resolved_chat_lineage_root(self.store.project_root(project_id))
            / f"{session_id}.jsonl"
        )

    def _active_path(self, project_id: str) -> Path:
        return self.store.project_root(project_id) / "active_session.json"

    def _prepare_host_plan_rehydration(
        self,
        project_id: str,
        session: SessionRecord,
        *,
        trigger: str,
        trigger_event_id: str,
        observed_artifact: dict[str, Any] | None = None,
        host_capability: str = "SUPPORTED",
        host_goal_active: bool | None = None,
        affected_plan_task_ids: list[str] | None = None,
        fixed_window_task_ids: list[str] | None = None,
        reuse_previous_window: bool = True,
    ) -> dict[str, Any] | None:
        """Prepare the exact host projection when a physical-final Plan exists."""

        host_task_id = str(
            session.metadata.get("current_host_session_id") or ""
        ).strip()
        goal_rows = cast(
            list[dict[str, Any]],
            self.store.backlog_status(project_id)
            .get("goal_projection", {})
            .get("rows", []),
        )
        if not host_task_id or not any(
            row.get("panel_role") == "PHYSICALLY_FINAL_HIL" for row in goal_rows
        ):
            return None
        persisted_window_task_ids = [
            str(task_id).strip()
            for task_id in cast(
                dict[str, Any], session.metadata.get("host_plan_window") or {}
            ).get("window_task_ids", [])
            if str(task_id).strip()
        ]
        explicit_window_task_ids = [
            str(task_id).strip()
            for task_id in fixed_window_task_ids or []
            if str(task_id).strip()
        ]
        if not explicit_window_task_ids and not persisted_window_task_ids:
            active_rows = [
                row
                for row in goal_rows
                if row.get("status") == "in_progress"
                and row.get("lifecycle_status") == "ACTIVE"
            ]
            if (
                len(active_rows) != 1
                or not goal_rows
                or active_rows[0].get("task_id") != goal_rows[0].get("task_id")
            ):
                # A physical-final Plan alone does not authorize choosing a host
                # batch. Only the first canonical activation may bind the initial
                # batch; an already-advanced Plan must supply persisted authority.
                return None
            persisted_window_task_ids = [str(row["task_id"]) for row in goal_rows[:9]]
            session.metadata["host_plan_window"] = {
                "schema": "evidence-lane.host-plan-window-state.v1",
                "window_task_ids": persisted_window_task_ids,
                "binding_source": "INITIAL_PLAN_ACTIVATION",
            }
            self._save(session)
        result = prepare_host_plan_rehydration(
            self.store.root,
            project_id=project_id,
            evidence_session_id=session.session_id,
            host_task_id=host_task_id,
            trigger=trigger,
            trigger_event_id=trigger_event_id,
            host_capability=host_capability,
            observed_artifact=observed_artifact,
            host_goal_active=host_goal_active,
            affected_plan_task_ids=affected_plan_task_ids,
            fixed_window_task_ids=fixed_window_task_ids,
            reuse_previous_window=reuse_previous_window,
        )
        receipt = cast(dict[str, Any], result["receipt"])
        projection = cast(dict[str, Any], receipt["projection"])
        session.metadata["host_plan_window"] = {
            "schema": "evidence-lane.host-plan-window-state.v1",
            "canonical_plan_sha256": projection.get("canonical_plan_sha256"),
            "executable_projection_sha256": projection.get(
                "executable_projection_sha256"
            ),
            "projection_sha256": projection.get("projection_sha256"),
            "full_row_start": projection.get("full_row_start"),
            "full_row_end": projection.get("full_row_end"),
            "total_executable_count": projection.get("total_executable_count"),
            "window_index": projection.get("window_index"),
            "row_start": projection.get("row_start"),
            "row_end": projection.get("row_end"),
            "item_count": projection.get("item_count"),
            "window_ui_fingerprint_sha256": projection.get(
                "window_ui_fingerprint_sha256"
            ),
            "window_task_ids": projection.get("window_task_ids"),
            "sole_active_row": projection.get("sole_active_row"),
            "completed_window_count": projection.get("completed_window_count"),
            "action": receipt.get("action"),
            "receipt_sha256": receipt.get("receipt_sha256"),
            "recorded_at": receipt.get("sealed_at"),
        }
        self._save(session)
        return result

    def bind_host_plan_window_after_plan_mutation(
        self,
        project_id: str,
        *,
        window_task_ids: list[str],
        linked_task_id: str,
    ) -> dict[str, Any] | None:
        """Persist the exact replacement batch chosen by a canonical Plan mutation."""

        active_path = self._active_path(project_id)
        if not active_path.is_file():
            return None
        active = json.loads(active_path.read_text(encoding="utf-8"))
        session_id = str(active.get("session_id") or "").strip()
        if not session_id:
            return None
        exact_task_ids = [str(task_id).strip() for task_id in window_task_ids]
        require(
            1 <= len(exact_task_ids) <= 9
            and all(exact_task_ids)
            and len(set(exact_task_ids)) == len(exact_task_ids),
            "HOST_PLAN_MUTATION_FIXED_BATCH_INVALID",
            "A canonical Plan mutation must bind one to nine unique host batch task IDs.",
            status="MISMATCH",
            task_count=len(exact_task_ids),
        )
        rows = cast(
            list[dict[str, Any]],
            self.store.backlog_status(project_id)
            .get("goal_projection", {})
            .get("rows", []),
        )
        row_indexes = {
            str(row.get("task_id") or ""): index for index, row in enumerate(rows)
        }
        require(
            exact_task_ids[0] in row_indexes,
            "HOST_PLAN_MUTATION_BATCH_START_MISSING",
            "The replacement host batch start is not in executable Plan authority.",
            status="MISMATCH",
            task_id=exact_task_ids[0],
        )
        start = row_indexes[exact_task_ids[0]]
        canonical_task_ids = [
            str(row["task_id"]) for row in rows[start : start + len(exact_task_ids)]
        ]
        require(
            canonical_task_ids == exact_task_ids,
            "HOST_PLAN_MUTATION_BATCH_NOT_CONTIGUOUS",
            "The replacement host batch must be the exact contiguous canonical Plan slice.",
            status="MISMATCH",
        )
        exact_linked_task_id = str(linked_task_id).strip()
        session = self.load(project_id, session_id)
        previous = cast(dict[str, Any], session.metadata.get("host_plan_window") or {})
        session.metadata["host_plan_window"] = {
            **previous,
            "schema": "evidence-lane.host-plan-window-state.v1",
            "window_task_ids": exact_task_ids,
            "binding_source": "CANONICAL_PLAN_STEER_MUTATION",
        }
        self._save(session)
        body = {
            "schema": "evidence-lane.host-plan-window-mutation-rebind.v1",
            "status": "PASS",
            "project_id": project_id,
            "session_id": session_id,
            "linked_task_id": exact_linked_task_id,
            "linked_task_in_fixed_batch": exact_linked_task_id in exact_task_ids,
            "window_task_ids": exact_task_ids,
            "binding_source": "CANONICAL_PLAN_STEER_MUTATION",
            "fallback_projector_used": False,
            "sliding_window_derived": False,
        }
        return {
            **body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(body)),
        }

    @staticmethod
    def _source_edit_authority(
        host: HostKind,
        client_can_edit_source: bool | None,
    ) -> str:
        if client_can_edit_source is not None:
            return "DIRECT" if client_can_edit_source else "USER_MEDIATED"
        return (
            "DIRECT"
            if host in {HostKind.CODEX_DESKTOP, HostKind.CODEX_CLI}
            else "USER_MEDIATED"
        )

    def _resolve_rollback_target(
        self,
        session: SessionRecord,
        rollback_to: str | None,
    ) -> tuple[str, str | None, bool, dict[str, Any]]:
        entry_pv = cast(str | None, session.metadata.get("entry_pv"))
        default_used = not bool(rollback_to and rollback_to.strip())
        host_session_id = str(
            session.metadata.get("current_host_session_id") or ""
        ).strip()
        resolution: dict[str, Any] | None = None
        if rollback_to and is_prompt_reference(rollback_to):
            resolution = PromptIndex(self.store.root).resolve(
                host_session_id=host_session_id,
                project_id=session.project_id,
                evidence_session_id=session.session_id,
                reference=rollback_to,
            )
            raw_target = resolution["entry_pv"]
        elif rollback_to:
            raw_target = rollback_to.strip()
            resolution = {
                "kind": "EXPLICIT_ROLLBACK_STATE_REF",
                "reference": rollback_to.strip(),
                "entry_pv": raw_target,
            }
        else:
            latest = (
                PromptIndex(self.store.root).latest_entry(
                    host_session_id=host_session_id,
                    project_id=session.project_id,
                    evidence_session_id=session.session_id,
                )
                if host_session_id
                else None
            )
            resolution = latest or {
                "kind": "SESSION_ENTRY",
                "reference": "BARE_ROLLBACK",
                "entry_pv": entry_pv,
            }
            raw_target = resolution.get("entry_pv")
        require(
            bool(raw_target),
            "ROLLBACK_ENTRY_TARGET_UNAVAILABLE",
            "Bare rollback requires a current prompt-entry PV or governed session "
            "entry PV. Indexed rollback requires a resolvable PROMPT or TURN record.",
            status="BLOCKED",
            entry_pv=entry_pv,
            host_session_id_available=bool(host_session_id),
        )
        target_pv = _normalize_pv_target(cast(str, raw_target))
        resolution["resolved_target"] = target_pv
        return target_pv, entry_pv, default_used, resolution

    def rollback_live_root_state(
        self,
        project_id: str,
        session_id: str,
        *,
        decided_by: str,
        rollback_mode: str = "LOGICAL_LIVE_ROOT_STATE",
        rollback_to: str | None = None,
        decision_id: str | None = None,
        confirmation: str | None = None,
        archive_path: str | None = None,
        expected_archive_sha256: str | None = None,
        restore_root: str | None = None,
        repository_path: str | None = None,
        branch: str | None = None,
        commit_sha: str | None = None,
        restore_workspace: str | None = None,
        target_plan_task_id: str | None = None,
    ) -> dict[str, Any]:
        """Select a Plan-stamped live-root state without archive or source mutation."""

        session = self.load(project_id, session_id)
        pointer = self.store.pointer(project_id)
        require(
            pointer.generation == session.accepted_pointer_generation,
            "ROLLBACK_SESSION_POINTER_STALE",
            "The accepted pointer changed after this session last synchronized.",
            status="STALE",
            expected_generation=session.accepted_pointer_generation,
            actual_generation=pointer.generation,
        )
        exact_mode = str(rollback_mode or "").strip().upper()
        require(
            exact_mode
            in {
                "LOGICAL_LIVE_ROOT_STATE",
                "HARD_ACCEPTED_ZIP_RESTORE",
                "GIT_BRANCH_COMMIT_RESTORE",
            },
            "ROLLBACK_MODE_INVALID",
            "Rollback mode must select one of the three existing authorities.",
            status="BLOCKED",
            rollback_mode=rollback_mode,
        )
        exact_decision_id = decision_id or prefixed_id("decision")
        if exact_mode == "HARD_ACCEPTED_ZIP_RESTORE":
            require(
                all(
                    str(value or "").strip()
                    for value in (
                        archive_path,
                        expected_archive_sha256,
                        restore_root,
                        target_plan_task_id,
                        confirmation,
                    )
                ),
                "HARD_ROLLBACK_ARGUMENTS_REQUIRED",
                "Hard restore requires ZIP, SHA-256, fresh root, Plan row, and confirmation.",
                status="BLOCKED",
            )
            preserved_candidate_id = session.candidate_id
            result = self.store.hard_restore_live_root(
                project_id,
                archive_path=cast(str, archive_path),
                expected_archive_sha256=cast(str, expected_archive_sha256),
                restore_root=cast(str, restore_root),
                target_plan_task_id=cast(str, target_plan_task_id),
                expected_pointer_generation=pointer.generation,
                selected_by=decided_by,
                decision_id=exact_decision_id,
                confirmation=cast(str, confirmation),
            )
            after = self.store.pointer(project_id)
            session.accepted_pv = after.accepted_pv
            session.accepted_pointer_generation = after.generation
            session.task = None
            session.candidate_id = None
            session.state = (
                SessionState.PVN_ACCEPTED
                if after.accepted_pv == "PV1"
                else SessionState.PVN1_ACCEPTED
            )
            session.metadata["last_rollback"] = result["receipt"]
            session.metadata["rollback_mode"] = exact_mode
            session.metadata["replan_required"] = True
            session.metadata["fresh_user_brief_required"] = True
            session.metadata["old_plan_continuation_allowed"] = False
            session.metadata["preserved_prior_candidate_id"] = (
                preserved_candidate_id
            )
            self._save(session)
            ChatLineage(self._lineage_path(project_id, session_id)).append(
                event_type="rollback.hard_restore.completed",
                visible_payload=result["receipt"],
                occurred_at=utc_now(),
                session_id=session_id,
            )
            return {**result, "session": session.as_dict()}
        if exact_mode == "GIT_BRANCH_COMMIT_RESTORE":
            require(
                all(
                    str(value or "").strip()
                    for value in (
                        repository_path,
                        branch,
                        commit_sha,
                        restore_workspace,
                        target_plan_task_id,
                        confirmation,
                    )
                ),
                "GIT_ROLLBACK_ARGUMENTS_REQUIRED",
                "Git restore requires repository, branch, commit, fresh workspace, Plan row, and confirmation.",
                status="BLOCKED",
            )
            preserved_candidate_id = session.candidate_id
            result = self.store.git_restore_live_root(
                project_id,
                repository_path=cast(str, repository_path),
                branch=cast(str, branch),
                commit_sha=cast(str, commit_sha),
                restore_workspace=cast(str, restore_workspace),
                target_plan_task_id=cast(str, target_plan_task_id),
                expected_pointer_generation=pointer.generation,
                selected_by=decided_by,
                decision_id=exact_decision_id,
                confirmation=cast(str, confirmation),
            )
            session.task = None
            session.candidate_id = None
            session.state = (
                SessionState.PVN_ACCEPTED
                if pointer.accepted_pv == "PV1"
                else SessionState.PVN1_ACCEPTED
            )
            session.metadata["last_rollback"] = result["receipt"]
            session.metadata["rollback_mode"] = exact_mode
            session.metadata["replan_required"] = True
            session.metadata["fresh_user_brief_required"] = True
            session.metadata["old_plan_continuation_allowed"] = False
            session.metadata["preserved_candidate_id"] = preserved_candidate_id
            session.metadata["repository_path"] = str(restore_workspace)
            self._save(session)
            ChatLineage(self._lineage_path(project_id, session_id)).append(
                event_type="rollback.git_restore.completed",
                visible_payload=result["receipt"],
                occurred_at=utc_now(),
                session_id=session_id,
            )
            return {**result, "session": session.as_dict()}
        target_ref, entry_pv, default_used, resolution = (
            self._resolve_rollback_target(session, rollback_to)
        )
        result = self.store.rollback_live_root_state(
            project_id,
            target_state_ref=target_ref,
            expected_pointer_generation=pointer.generation,
            decided_by=decided_by,
            decision_id=exact_decision_id,
            candidate_id=session.candidate_id,
            resolution_reference={
                **resolution,
                "session_entry_pv": entry_pv,
                "default_entry_target_used": default_used,
            },
        )
        session.metadata["last_rollback"] = result["receipt"]
        session.metadata["rollback_state_cursor"] = result["cursor"]
        session.metadata.setdefault("rollback_history", []).append(
            result["receipt"]
        )
        session.metadata["accepted_pv_query_scope"] = (
            "LIVE_ROOT_PLAN_STAMPED_ROLLBACK_STATE"
        )
        self._save(session)
        ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="rollback.logical_state.selected",
            visible_payload=result["receipt"],
            occurred_at=utc_now(),
            session_id=session_id,
            task_id=str(
                session.metadata.get("active_backlog_task_id")
                or (session.task or {}).get("task_id")
                or ""
            )
            or None,
            run_id=str(session.metadata.get("run_id") or "") or None,
        )
        return {
            **result,
            "decision": result["receipt"],
            "session": session.as_dict(),
            "candidate_preserved_unaccepted": session.candidate_id,
            "task_preserved": session.task,
            "session_state_preserved": session.state.value,
        }

    def _verify_repository_matches_identity(
        self,
        project_id: str,
        project_identity: dict[str, Any],
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
        expected = project_identity["repository"]
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
                "display_name": "Evidence Lane",
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

    @staticmethod
    def session_snapshot_sha256(session: SessionRecord) -> str:
        return sha256_bytes(canonical_json_bytes(session.as_dict()))

    def pointer_snapshot_sha256(self, project_id: str) -> str:
        return sha256_bytes(
            canonical_json_bytes(self.store.pointer(project_id).as_dict())
        )

    def _plan_normalization_path(
        self,
        project_id: str,
        transition_id: str,
    ) -> Path:
        exact = transition_id.strip()
        require(
            bool(exact)
            and len(exact) <= 96
            and all(character in _SAFE_ID_CHARACTERS for character in exact),
            "PLAN_NORMALIZATION_TRANSITION_ID_INVALID",
            "A Plan normalization requires one stable public-safe transition ID.",
            status="BLOCKED",
        )
        root = resolved_plan_auxiliary_path(
            self.store.project_root(project_id), "plan_normalization"
        )
        path = (root / f"{exact}.json").resolve()
        path.relative_to(root)
        return path

    def _load_plan_normalization_journal(
        self,
        project_id: str,
        transition_id: str,
    ) -> dict[str, Any] | None:
        path = self._plan_normalization_path(project_id, transition_id)
        if not path.is_file():
            return None
        try:
            journal = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceLaneError(
                "PLAN_NORMALIZATION_JOURNAL_INVALID",
                "The Plan-normalization transaction journal is unreadable.",
                status="FAIL",
                details={"path": str(path), "error": type(exc).__name__},
            ) from exc
        require(
            journal.get("schema") == _PLAN_NORMALIZATION_SCHEMA
            and journal.get("project_id") == project_id
            and journal.get("transition_id") == transition_id
            and journal.get("phase") in _PLAN_NORMALIZATION_PHASES,
            "PLAN_NORMALIZATION_JOURNAL_INVALID",
            "The Plan-normalization transaction journal has an invalid shape.",
            status="FAIL",
            path=str(path),
        )
        return cast(dict[str, Any], journal)

    def _write_plan_normalization_journal(
        self,
        project_id: str,
        transition_id: str,
        journal: dict[str, Any],
    ) -> None:
        path = self._plan_normalization_path(project_id, transition_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, journal)

    def _active_contract_rebind_path(
        self,
        project_id: str,
        rebind_id: str,
    ) -> Path:
        exact = str(rebind_id or "").strip()
        require(
            bool(exact)
            and len(exact) <= 96
            and all(character in _SAFE_ID_CHARACTERS for character in exact),
            "ACTIVE_CONTRACT_REBIND_ID_INVALID",
            "An active-contract rebind requires one stable public-safe ID.",
            status="BLOCKED",
        )
        root = self.store.project_root(project_id)
        path = (
            root / "receipts" / "active-contract-rebindings" / f"{exact}.json"
        ).resolve()
        path.relative_to(root)
        return path

    def _load_active_contract_rebind_journal(
        self,
        project_id: str,
        rebind_id: str,
    ) -> dict[str, Any] | None:
        path = self._active_contract_rebind_path(project_id, rebind_id)
        if not path.is_file():
            return None
        try:
            journal = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceLaneError(
                "ACTIVE_CONTRACT_REBIND_JOURNAL_INVALID",
                "The active-contract rebind journal is unreadable.",
                status="FAIL",
                details={"path": str(path), "error": type(exc).__name__},
            ) from exc
        require(
            journal.get("schema") == _ACTIVE_CONTRACT_REBIND_SCHEMA
            and journal.get("project_id") == project_id
            and journal.get("rebind_id") == rebind_id
            and journal.get("phase") in _ACTIVE_CONTRACT_REBIND_PHASES,
            "ACTIVE_CONTRACT_REBIND_JOURNAL_INVALID",
            "The active-contract rebind journal has an invalid shape.",
            status="FAIL",
            path=str(path),
        )
        return cast(dict[str, Any], journal)

    def _write_active_contract_rebind_journal(
        self,
        project_id: str,
        rebind_id: str,
        journal: dict[str, Any],
    ) -> None:
        path = self._active_contract_rebind_path(project_id, rebind_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, journal)

    def _correct_plan_normalization(
        self,
        project_id: str,
        *,
        tasks: list[dict[str, Any]],
        planned_by: str,
        plan_id: str,
        transition: dict[str, Any],
    ) -> dict[str, Any]:
        """Correct one committed normalization without deleting any history."""

        require(
            tasks == [],
            "PLAN_NORMALIZATION_CORRECTION_TASK_SET_INVALID",
            "A normalization correction restores existing rows and must append no task.",
            status="BLOCKED",
        )
        transition_id = str(transition.get("transition_id") or "").strip()
        original_transition_id = str(
            transition.get("correction_of_transition_id") or ""
        ).strip()
        session_id = str(transition.get("session_id") or "").strip()
        restored_task_id = str(transition.get("restored_active_task_id") or "").strip()
        mistaken_task_id = str(
            transition.get("mistaken_replacement_task_id") or ""
        ).strip()
        expected_final_task_id = str(
            transition.get("expected_physically_final_task_id") or ""
        ).strip()
        correction_delta_id = str(transition.get("correction_delta_id") or "").strip()
        goal_row_offset = transition.get("goal_row_offset")
        expected_plan_sha256 = _require_sha256(
            transition.get("expected_canonical_plan_sha256"),
            field="expected_canonical_plan_sha256",
        )
        expected_session_sha256 = _require_sha256(
            transition.get("expected_session_sha256"),
            field="expected_session_sha256",
        )
        expected_pointer_sha256 = _require_sha256(
            transition.get("expected_pointer_sha256"),
            field="expected_pointer_sha256",
        )
        expected_original_request_sha256 = _require_sha256(
            transition.get("expected_original_request_sha256"),
            field="expected_original_request_sha256",
        )
        expected_original_result_sha256 = _require_sha256(
            transition.get("expected_original_result_sha256"),
            field="expected_original_result_sha256",
        )
        correction_delta_sha256 = _require_sha256(
            transition.get("correction_delta_sha256"),
            field="correction_delta_sha256",
        )
        correction_receipt_sha256 = _require_sha256(
            transition.get("correction_receipt_sha256"),
            field="correction_receipt_sha256",
        )
        self._plan_normalization_path(project_id, transition_id)
        for field, value in (
            ("correction_of_transition_id", original_transition_id),
            ("session_id", session_id),
            ("restored_active_task_id", restored_task_id),
            ("mistaken_replacement_task_id", mistaken_task_id),
            ("expected_physically_final_task_id", expected_final_task_id),
            ("correction_delta_id", correction_delta_id),
        ):
            require(
                bool(value),
                "PLAN_NORMALIZATION_CORRECTION_CONTRACT_INVALID",
                "The normalization correction is missing one exact identity.",
                status="BLOCKED",
                field=field,
            )
        require(
            transition_id != original_transition_id
            and restored_task_id != mistaken_task_id,
            "PLAN_NORMALIZATION_CORRECTION_IDENTITY_CONFLICT",
            "The correction, original transition, restored row, and mistaken row must be distinct.",
            status="BLOCKED",
        )
        require(
            isinstance(goal_row_offset, int) and goal_row_offset >= 0,
            "PLAN_NORMALIZATION_CORRECTION_ROW_OFFSET_INVALID",
            "The correction requires one non-negative current-execution row offset.",
            status="BLOCKED",
            goal_row_offset=goal_row_offset,
        )
        goal_row_offset = cast(int, goal_row_offset)
        require(
            transition.get("expected_candidate_absent") is True
            and transition.get("expected_pending_hil") is False,
            "PLAN_NORMALIZATION_UNACCEPTED_STATE_EXPECTATION_REQUIRED",
            "A correction must explicitly expect no candidate and pending_hil=false.",
            status="BLOCKED",
        )

        original_path = self._plan_normalization_path(
            project_id,
            original_transition_id,
        )
        original = self._load_plan_normalization_journal(
            project_id,
            original_transition_id,
        )
        require(
            isinstance(original, dict)
            and original.get("phase") == "COMMITTED"
            and original.get("status") == "PASS"
            and original.get("request_sha256") == expected_original_request_sha256
            and original.get("result_sha256") == expected_original_result_sha256
            and original.get("session_id") == session_id
            and original.get("old_active_task_id") == restored_task_id
            and original.get("replacement_task_id") == mistaken_task_id
            and original.get("result", {}).get("active_task_id") == mistaken_task_id,
            "PLAN_NORMALIZATION_CORRECTION_ORIGINAL_MISMATCH",
            "The correction does not bind the exact committed normalization journal.",
            status="MISMATCH",
            correction_of_transition_id=original_transition_id,
        )
        original = cast(dict[str, Any], original)
        original_journal_sha256 = sha256_bytes(original_path.read_bytes())
        request_body = {
            "project_id": project_id,
            "plan_id": plan_id,
            "planned_by": planned_by.strip(),
            "tasks": tasks,
            "normalization_transition": transition,
        }
        request_sha256 = sha256_bytes(canonical_json_bytes(request_body))
        journal = self._load_plan_normalization_journal(
            project_id,
            transition_id,
        )
        if journal is not None:
            require(
                journal.get("request_sha256") == request_sha256,
                "PLAN_NORMALIZATION_REPLAY_CONFLICT",
                "The correction transition ID already binds a different request.",
                status="BLOCKED",
                transition_id=transition_id,
            )
        else:
            backlog = self.store.backlog_status(project_id)
            session = self.load(project_id, session_id)
            pointer = self.store.pointer(project_id)
            current_plan_sha256 = str(
                backlog["canonical_plan_projection"]["projection_sha256"]
            )
            current_session_sha256 = self.session_snapshot_sha256(session)
            current_pointer_sha256 = sha256_bytes(
                canonical_json_bytes(pointer.as_dict())
            )
            mismatches = {
                key: {"expected": expected, "current": current}
                for key, expected, current in (
                    (
                        "canonical_plan_sha256",
                        expected_plan_sha256,
                        current_plan_sha256,
                    ),
                    ("session_sha256", expected_session_sha256, current_session_sha256),
                    ("pointer_sha256", expected_pointer_sha256, current_pointer_sha256),
                )
                if expected != current
            }
            require(
                not mismatches,
                "PLAN_NORMALIZATION_CORRECTION_PRECONDITION_MISMATCH",
                "Plan, session, and pointer preconditions must match before correction writes anything.",
                status="MISMATCH",
                mismatches=mismatches,
                writes_performed=False,
            )
            tasks_by_id = {str(row["task_id"]): row for row in backlog["tasks"]}
            require(
                [str(row["task_id"]) for row in backlog["active"]] == [mistaken_task_id]
                and tasks_by_id.get(restored_task_id, {}).get("status") == "SUPERSEDED"
                and session.metadata.get("active_backlog_task_id") == mistaken_task_id
                and isinstance(session.task, dict)
                and session.task.get("task_id") == mistaken_task_id,
                "PLAN_NORMALIZATION_CORRECTION_ACTIVE_BINDING_MISMATCH",
                "The correction requires the exact mistaken active row and superseded original row.",
                status="MISMATCH",
                writes_performed=False,
            )
            correction_delta = next(
                (
                    delta
                    for row in backlog["tasks"]
                    for delta in row.get("steer_deltas", [])
                    if delta.get("delta_id") == correction_delta_id
                ),
                None,
            )
            require(
                isinstance(correction_delta, dict)
                and sha256_bytes(
                    str(correction_delta.get("text") or "").encode("utf-8")
                )
                == correction_delta_sha256,
                "PLAN_NORMALIZATION_CORRECTION_DELTA_MISMATCH",
                "The exact user correction Delta is not present in the immutable Plan ledger.",
                status="MISMATCH",
                correction_delta_id=correction_delta_id,
                writes_performed=False,
            )
            require(
                session.candidate_id is None
                and not bool(session.metadata.get("pending_hil"))
                and not isinstance(session.metadata.get("pending_task"), dict),
                "PLAN_NORMALIZATION_CANDIDATE_OR_HIL_PRESENT",
                "Plan correction cannot run with a candidate, pending HIL, or pending follow-up.",
                status="BLOCKED",
                writes_performed=False,
            )
            require(
                pointer.generation == session.accepted_pointer_generation
                and pointer.accepted_pv == session.accepted_pv,
                "PLAN_NORMALIZATION_SESSION_POINTER_STALE",
                "The live session and accepted pointer diverged before correction.",
                status="STALE",
                writes_performed=False,
            )
            expected_total = transition.get("expected_result_task_count")
            require(
                isinstance(expected_total, int)
                and len(backlog["tasks"]) == expected_total
                and str(backlog["tasks"][-1]["task_id"]) == expected_final_task_id
                and str(backlog["tasks"][-1].get("panel_role") or "").upper()
                == "PHYSICALLY_FINAL_HIL",
                "PLAN_NORMALIZATION_CORRECTION_PLAN_SHAPE_MISMATCH",
                "The correction requires the exact row count and physically final HIL row.",
                status="MISMATCH",
                writes_performed=False,
            )
            task_ids = [str(row["task_id"]) for row in backlog["tasks"]]
            now = utc_now()
            journal = {
                "schema": _PLAN_NORMALIZATION_SCHEMA,
                "status": "IN_PROGRESS",
                "project_id": project_id,
                "transition_id": transition_id,
                "correction_of_transition_id": original_transition_id,
                "plan_id": plan_id,
                "session_id": session_id,
                "request_sha256": request_sha256,
                "correction_receipt_sha256": correction_receipt_sha256,
                "original_journal_sha256": original_journal_sha256,
                "baseline": {
                    "canonical_plan_sha256": current_plan_sha256,
                    "session_sha256": current_session_sha256,
                    "pointer_sha256": current_pointer_sha256,
                    "task_count": len(backlog["tasks"]),
                    "task_ids_sha256": sha256_bytes(canonical_json_bytes(task_ids)),
                    "event_count": backlog["event_count"],
                    "event_head_sha256": backlog["event_head_sha256"],
                    "active_task_id": mistaken_task_id,
                    "restored_task_status": "SUPERSEDED",
                    "candidate_absent": True,
                    "pending_hil": False,
                    "goal_row_offset": backlog.get("goal_row_offset", 0),
                },
                "mistaken_replacement_task_id": mistaken_task_id,
                "restored_active_task_id": restored_task_id,
                "expected_physically_final_task_id": expected_final_task_id,
                "correction_delta_id": correction_delta_id,
                "correction_delta_sha256": correction_delta_sha256,
                "started_at": now,
                "phase": "PREPARED",
                "phase_updated_at": now,
            }
            self._write_plan_normalization_journal(
                project_id,
                transition_id,
                journal,
            )

        def advance(phase: str, **details: Any) -> None:
            journal["phase"] = phase
            journal["phase_updated_at"] = utc_now()
            journal.update(details)
            self._write_plan_normalization_journal(
                project_id,
                transition_id,
                journal,
            )

        if journal["phase"] == "PREPARED":
            corrected = self.store.correct_plan_normalization(
                project_id,
                original_transition_id=original_transition_id,
                correction_transition_id=transition_id,
                mistaken_task_id=mistaken_task_id,
                restored_task_id=restored_task_id,
                session_id=session_id,
                runtime_task_id=restored_task_id,
                corrected_by=planned_by.strip(),
                correction_receipt_sha256=correction_receipt_sha256,
                goal_row_offset=goal_row_offset,
            )
            advance(
                "PLAN_CORRECTED",
                corrected_plan_sha256=corrected["canonical_plan_projection"][
                    "projection_sha256"
                ],
                corrected_event_head_sha256=corrected["event_head_sha256"],
            )

        if journal["phase"] == "PLAN_CORRECTED":
            session = self.load(project_id, session_id)
            backlog = self.store.backlog_status(project_id)
            restored = next(
                row for row in backlog["tasks"] if row["task_id"] == restored_task_id
            )
            already_rebound = (
                session.metadata.get("active_backlog_task_id") == restored_task_id
                and isinstance(session.task, dict)
                and session.task.get("task_id") == restored_task_id
            )
            if not already_rebound:
                require(
                    session.metadata.get("active_backlog_task_id") == mistaken_task_id
                    and isinstance(session.task, dict)
                    and session.task.get("task_id") == mistaken_task_id,
                    "PLAN_NORMALIZATION_CORRECTION_SESSION_REBIND_MISMATCH",
                    "Crash recovery found neither the mistaken nor exact restored session binding.",
                    status="MISMATCH",
                )
                prior_task = cast(dict[str, Any], session.task)
                restored_contract = classify_task(
                    task_id=restored_task_id,
                    task_class=str(restored["task_class"]),
                    requested_outcome=str(restored["requested_outcome"]),
                    permitted_paths=cast(list[str], restored["permitted_paths"]),
                    permitted_tools=cast(list[str], restored["permitted_tools"]),
                    acceptance_checks=cast(list[str], restored["acceptance_checks"]),
                    stop_condition=str(restored["stop_condition"]),
                ).as_dict()
                rebound_at = utc_now()
                run_id = f"run_normcorr_{request_sha256[:20].lower()}"
                rebind_body = {
                    "schema": "evidence-lane.plan-normalization-correction-rebind.v1",
                    "transition_id": transition_id,
                    "correction_of_transition_id": original_transition_id,
                    "plan_id": plan_id,
                    "session_id": session_id,
                    "mistaken_backlog_task_id": mistaken_task_id,
                    "mistaken_runtime_task_id": prior_task.get("task_id"),
                    "restored_backlog_task_id": restored_task_id,
                    "restored_runtime_task_id": restored_task_id,
                    "correction_receipt_sha256": correction_receipt_sha256,
                    "candidate_created": False,
                    "pending_hil": False,
                    "pointer_moved": False,
                    "rebound_at": rebound_at,
                }
                rebind_receipt = {
                    **rebind_body,
                    "receipt_sha256": sha256_bytes(canonical_json_bytes(rebind_body)),
                }
                session.metadata.setdefault(
                    "plan_normalization_corrections", []
                ).append(rebind_receipt)
                session.metadata["active_backlog_task_id"] = restored_task_id
                session.metadata["run_id"] = run_id
                session.metadata["source_update_confirmed"] = False
                session.task = restored_contract
                active_mode_binding = session.metadata.get("active_mode_binding")
                if isinstance(active_mode_binding, dict) and isinstance(
                    active_mode_binding.get("mode_governance"), dict
                ):
                    governance = cast(
                        dict[str, Any], active_mode_binding["mode_governance"]
                    )
                    validate_mode_governance_selection(governance)
                    mode_body = {
                        "schema": "evidence-lane.task-mode-binding.v1",
                        "task_id": restored_task_id,
                        "selected_mode_ids": list(
                            active_mode_binding["selected_mode_ids"]
                        ),
                        "mode_intersection": active_mode_binding["mode_intersection"],
                        "canonical_lanes": list(active_mode_binding["canonical_lanes"]),
                        "selection_source": active_mode_binding["selection_source"],
                        "mode_governance": governance,
                        "selection_receipt_sha256": active_mode_binding[
                            "binding_receipt_sha256"
                        ],
                        "bound_at_task_classification": rebound_at,
                        "lifecycle_effect": "NONE",
                        "candidate_created": False,
                        "pointer_moved": False,
                        "hil_approval_inferred": False,
                    }
                    session.metadata["task_mode_binding"] = {
                        **mode_body,
                        "binding_receipt_sha256": sha256_bytes(
                            canonical_json_bytes(mode_body)
                        ),
                    }
                self._save(session)
                ChatLineage(self._lineage_path(project_id, session_id)).append(
                    event_type="plan.normalization.correction.rebound",
                    visible_payload=rebind_receipt,
                    occurred_at=rebound_at,
                    session_id=session_id,
                    task_id=restored_task_id,
                    run_id=run_id,
                    event_id=f"{transition_id}__session_rebound",
                )
            advance(
                "SESSION_REBOUND",
                rebound_session_sha256=self.session_snapshot_sha256(
                    self.load(project_id, session_id)
                ),
            )

        backlog = self.store.backlog_status(project_id)
        session = self.load(project_id, session_id)
        pointer_sha256 = self.pointer_snapshot_sha256(project_id)
        expected_total = transition.get("expected_result_task_count")
        task_ids = [str(row["task_id"]) for row in backlog["tasks"]]
        tasks_after = {str(row["task_id"]): row for row in backlog["tasks"]}
        original_superseded = cast(list[str], original["superseded_task_ids"])
        failed_checks = {
            "task_count_unchanged": isinstance(expected_total, int)
            and len(backlog["tasks"]) == expected_total,
            "task_ids_unchanged": sha256_bytes(canonical_json_bytes(task_ids))
            == journal["baseline"]["task_ids_sha256"],
            "sole_active_restored": [str(row["task_id"]) for row in backlog["active"]]
            == [restored_task_id],
            "mistaken_row_superseded": tasks_after.get(mistaken_task_id, {}).get(
                "status"
            )
            == "SUPERSEDED",
            "other_original_supersessions_preserved": all(
                task_id == restored_task_id
                or tasks_after.get(task_id, {}).get("status") == "SUPERSEDED"
                for task_id in original_superseded
            ),
            "session_backlog_binding": session.metadata.get("active_backlog_task_id")
            == restored_task_id,
            "session_runtime_binding": isinstance(session.task, dict)
            and session.task.get("task_id") == restored_task_id,
            "candidate_absent": session.candidate_id is None,
            "pending_hil_false": not bool(session.metadata.get("pending_hil")),
            "pointer_unchanged": pointer_sha256 == expected_pointer_sha256,
            "exactly_two_plan_events_appended": backlog["event_count"]
            == int(journal["baseline"]["event_count"]) + 2,
            "physically_final": str(backlog["tasks"][-1]["task_id"])
            == expected_final_task_id
            and str(backlog["tasks"][-1].get("panel_role") or "").upper()
            == "PHYSICALLY_FINAL_HIL",
            "original_journal_unchanged": sha256_bytes(original_path.read_bytes())
            == original_journal_sha256,
            "goal_row_range": backlog["goal_projection"].get("row_offset")
            == goal_row_offset
            and backlog["goal_projection"].get("row_start") == goal_row_offset + 1
            and backlog["goal_projection"].get("row_end")
            == goal_row_offset + int(backlog["goal_projection"]["task_count"]),
        }
        require(
            all(failed_checks.values()),
            "PLAN_NORMALIZATION_CORRECTION_COMMIT_VERIFICATION_FAILED",
            "The correction cannot commit until Plan, session, pointer, history, and final-HIL invariants all pass.",
            status="FAIL",
            failed_checks=sorted(
                key for key, passed in failed_checks.items() if not passed
            ),
        )
        final_body = {
            "canonical_plan_sha256": backlog["canonical_plan_projection"][
                "projection_sha256"
            ],
            "goal_projection_sha256": backlog["goal_projection"]["projection_sha256"],
            "history_projection_sha256": backlog["history_projection"][
                "projection_sha256"
            ],
            "plan_runtime_sqlite_sha256": backlog["plan_runtime_projection"][
                "sqlite_sha256"
            ],
            "plan_runtime_projection_content_sha256": backlog[
                "plan_runtime_projection"
            ]["projection_content_sha256"],
            "event_count": backlog["event_count"],
            "event_head_sha256": backlog["event_head_sha256"],
            "session_sha256": self.session_snapshot_sha256(session),
            "pointer_sha256": pointer_sha256,
            "task_count": len(backlog["tasks"]),
            "counts": backlog["counts"],
            "active_task_id": restored_task_id,
            "superseded_mistaken_task_id": mistaken_task_id,
            "physically_final_task_id": backlog["tasks"][-1]["task_id"],
            "candidate_created": False,
            "pending_hil": False,
            "pointer_moved": False,
            "rows_appended": 0,
            "plan_events_appended": 2,
            "goal_row_offset": goal_row_offset,
            "goal_row_start": backlog["goal_projection"].get("row_start"),
            "goal_row_end": backlog["goal_projection"].get("row_end"),
        }
        if journal["phase"] != "COMMITTED":
            advance(
                "COMMITTED",
                status="PASS",
                committed_at=utc_now(),
                result=final_body,
                result_sha256=sha256_bytes(canonical_json_bytes(final_body)),
            )
            idempotent_replay = False
        else:
            require(
                journal.get("result") == final_body,
                "PLAN_NORMALIZATION_COMMITTED_STATE_DRIFT",
                "The committed correction no longer matches its sealed result.",
                status="MISMATCH",
                transition_id=transition_id,
            )
            idempotent_replay = True
        return {
            **backlog,
            "normalization_transition": {
                **final_body,
                "status": "PASS",
                "schema": _PLAN_NORMALIZATION_SCHEMA,
                "transition_id": transition_id,
                "correction_of_transition_id": original_transition_id,
                "plan_id": plan_id,
                "correction_receipt_sha256": correction_receipt_sha256,
                "journal_phase": journal["phase"],
                "journal_path": str(
                    self._plan_normalization_path(project_id, transition_id)
                ),
                "request_sha256": request_sha256,
                "result_sha256": journal["result_sha256"],
                "idempotent_replay": idempotent_replay,
                "crash_recoverable": True,
                "writes_on_precondition_mismatch": 0,
            },
        }

    def normalize_plan_tasks(
        self,
        project_id: str,
        *,
        tasks: list[dict[str, Any]],
        planned_by: str,
        plan_id: str,
        normalization_transition: dict[str, Any],
    ) -> dict[str, Any]:
        """Append and bind one approved Plan normalization as a recoverable unit.

        The backlog and session are separate durable authorities, so the journal is
        the crash boundary: every phase is replayable, while every precondition is
        checked before the first write. A successful replay adds no Plan event,
        session mutation, candidate, HIL, or pointer movement.
        """

        require(
            isinstance(normalization_transition, dict),
            "PLAN_NORMALIZATION_CONTRACT_INVALID",
            "The optional normalization transition must be one structured contract.",
            status="BLOCKED",
        )
        transition = dict(normalization_transition)
        if transition.get("correction_of_transition_id"):
            return self._correct_plan_normalization(
                project_id,
                tasks=tasks,
                planned_by=planned_by,
                plan_id=plan_id,
                transition=transition,
            )
        transition_id = str(transition.get("transition_id") or "").strip()
        session_id = str(transition.get("session_id") or "").strip()
        old_active_task_id = str(transition.get("old_active_task_id") or "").strip()
        replacement_task_id = str(transition.get("replacement_task_id") or "").strip()
        review_task_id = str(transition.get("approved_review_gate_id") or "").strip()
        expected_plan_sha256 = _require_sha256(
            transition.get("expected_canonical_plan_sha256"),
            field="expected_canonical_plan_sha256",
        )
        expected_session_sha256 = _require_sha256(
            transition.get("expected_session_sha256"),
            field="expected_session_sha256",
        )
        expected_pointer_sha256 = _require_sha256(
            transition.get("expected_pointer_sha256"),
            field="expected_pointer_sha256",
        )
        approval_receipt_sha256 = _require_sha256(
            transition.get("approval_receipt_sha256"),
            field="approval_receipt_sha256",
        )
        self._plan_normalization_path(project_id, transition_id)
        for field, value in (
            ("session_id", session_id),
            ("old_active_task_id", old_active_task_id),
            ("replacement_task_id", replacement_task_id),
            ("approved_review_gate_id", review_task_id),
        ):
            require(
                bool(value),
                "PLAN_NORMALIZATION_CONTRACT_INVALID",
                "The Plan-normalization transition is missing one exact identity.",
                status="BLOCKED",
                field=field,
            )
        require(
            transition.get("expected_candidate_absent") is True
            and transition.get("expected_pending_hil") is False,
            "PLAN_NORMALIZATION_UNACCEPTED_STATE_EXPECTATION_REQUIRED",
            "Normalization must explicitly expect no candidate and pending_hil=false.",
            status="BLOCKED",
        )

        task_ids = [str(task.get("task_id") or "") for task in tasks]
        require(
            review_task_id in task_ids
            and replacement_task_id in task_ids
            and len(task_ids) == len(set(task_ids)),
            "PLAN_NORMALIZATION_TASK_SET_INVALID",
            "The exact approved review and replacement rows must be unique members of the appended plan.",
            status="BLOCKED",
            review_task_id=review_task_id,
            replacement_task_id=replacement_task_id,
        )
        tasks_by_id = {str(task["task_id"]): task for task in tasks}
        require(
            str(tasks_by_id[replacement_task_id].get("supersedes_task_id") or "")
            == old_active_task_id,
            "PLAN_NORMALIZATION_ACTIVE_SUCCESSOR_MISMATCH",
            "The replacement row must explicitly supersede the old active row.",
            status="MISMATCH",
        )
        superseded_task_ids = [
            str(task.get("supersedes_task_id") or "").strip()
            for task in tasks
            if str(task.get("supersedes_task_id") or "").strip()
        ]
        require(
            len(superseded_task_ids) == len(set(superseded_task_ids)),
            "PLAN_NORMALIZATION_DUPLICATE_SUPERSEDE_AUTHORITY",
            "One old executable row may map to only one authoritative successor row.",
            status="BLOCKED",
            duplicated_task_ids=sorted(
                task_id
                for task_id in set(superseded_task_ids)
                if superseded_task_ids.count(task_id) > 1
            ),
        )
        expected_superseded = transition.get("expected_superseded_task_ids")
        if expected_superseded is not None:
            require(
                isinstance(expected_superseded, list)
                and all(isinstance(value, str) for value in expected_superseded)
                and superseded_task_ids == expected_superseded,
                "PLAN_NORMALIZATION_SUPERSEDE_SET_MISMATCH",
                "The executable supersede set differs from the approved ordered set.",
                status="MISMATCH",
                expected=expected_superseded,
                supplied=superseded_task_ids,
            )
        final_roles = [
            index
            for index, task in enumerate(tasks, start=1)
            if str(task.get("panel_role") or "").strip().upper()
            == "PHYSICALLY_FINAL_HIL"
        ]
        require(
            not final_roles or final_roles == [len(tasks)],
            "PLAN_NORMALIZATION_FINAL_HIL_POSITION_INVALID",
            "The normalized six-way HIL must remain physically final.",
            status="BLOCKED",
            final_role_positions=final_roles,
            task_count=len(tasks),
        )

        request_body = {
            "project_id": project_id,
            "plan_id": plan_id,
            "planned_by": planned_by.strip(),
            "tasks": tasks,
            "normalization_transition": transition,
        }
        request_sha256 = sha256_bytes(canonical_json_bytes(request_body))
        journal = self._load_plan_normalization_journal(
            project_id,
            transition_id,
        )
        if journal is not None:
            require(
                journal.get("request_sha256") == request_sha256,
                "PLAN_NORMALIZATION_REPLAY_CONFLICT",
                "The transition ID already binds a different normalization request.",
                status="BLOCKED",
                transition_id=transition_id,
            )
        else:
            backlog = self.store.backlog_status(project_id)
            session = self.load(project_id, session_id)
            pointer = self.store.pointer(project_id)
            current_plan_sha256 = str(
                backlog["canonical_plan_projection"]["projection_sha256"]
            )
            current_session_sha256 = self.session_snapshot_sha256(session)
            current_pointer_sha256 = sha256_bytes(
                canonical_json_bytes(pointer.as_dict())
            )
            mismatches = {
                key: {"expected": expected, "current": current}
                for key, expected, current in (
                    (
                        "canonical_plan_sha256",
                        expected_plan_sha256,
                        current_plan_sha256,
                    ),
                    (
                        "session_sha256",
                        expected_session_sha256,
                        current_session_sha256,
                    ),
                    (
                        "pointer_sha256",
                        expected_pointer_sha256,
                        current_pointer_sha256,
                    ),
                )
                if expected != current
            }
            require(
                not mismatches,
                "PLAN_NORMALIZATION_PRECONDITION_MISMATCH",
                "Plan, session, and pointer preconditions must all match before normalization writes anything.",
                status="MISMATCH",
                mismatches=mismatches,
                writes_performed=False,
            )
            active_ids = [str(row["task_id"]) for row in backlog["active"]]
            require(
                active_ids == [old_active_task_id]
                and session.metadata.get("active_backlog_task_id") == old_active_task_id
                and isinstance(session.task, dict),
                "PLAN_NORMALIZATION_ACTIVE_BINDING_MISMATCH",
                "The native active Plan row and live session task must match the old active identity.",
                status="MISMATCH",
                active_task_ids=active_ids,
                session_backlog_task_id=session.metadata.get("active_backlog_task_id"),
                writes_performed=False,
            )
            current_tasks = {str(row["task_id"]): row for row in backlog["tasks"]}
            invalid_supersedes = {
                task_id: (
                    current_tasks.get(task_id, {}).get("status")
                    if task_id in current_tasks
                    else "MISSING"
                )
                for task_id in superseded_task_ids
                if task_id not in current_tasks
                or current_tasks[task_id].get("status") not in {"ACTIVE", "QUEUED"}
            }
            require(
                not invalid_supersedes,
                "PLAN_NORMALIZATION_EXECUTABLE_SET_MISMATCH",
                "Only currently active or queued executable rows may be superseded.",
                status="MISMATCH",
                invalid_task_statuses=invalid_supersedes,
                writes_performed=False,
            )
            require(
                session.candidate_id is None
                and not bool(session.metadata.get("pending_hil"))
                and not isinstance(session.metadata.get("pending_task"), dict),
                "PLAN_NORMALIZATION_CANDIDATE_OR_HIL_PRESENT",
                "Plan normalization cannot run with a candidate, pending HIL, or pending HIL follow-up.",
                status="BLOCKED",
                candidate_id=session.candidate_id,
                pending_hil=bool(session.metadata.get("pending_hil")),
                pending_task_present=isinstance(
                    session.metadata.get("pending_task"), dict
                ),
                writes_performed=False,
            )
            require(
                pointer.generation == session.accepted_pointer_generation
                and pointer.accepted_pv == session.accepted_pv,
                "PLAN_NORMALIZATION_SESSION_POINTER_STALE",
                "The live session and accepted pointer diverged before normalization.",
                status="STALE",
                pointer=pointer.as_dict(),
                session_accepted_pv=session.accepted_pv,
                session_pointer_generation=session.accepted_pointer_generation,
                writes_performed=False,
            )
            now = utc_now()
            journal = {
                "schema": _PLAN_NORMALIZATION_SCHEMA,
                "status": "IN_PROGRESS",
                "project_id": project_id,
                "transition_id": transition_id,
                "plan_id": plan_id,
                "session_id": session_id,
                "request_sha256": request_sha256,
                "approval_receipt_sha256": approval_receipt_sha256,
                "baseline": {
                    "canonical_plan_sha256": current_plan_sha256,
                    "session_sha256": current_session_sha256,
                    "pointer_sha256": current_pointer_sha256,
                    "task_count": len(backlog["tasks"]),
                    "event_count": backlog["event_count"],
                    "event_head_sha256": backlog["event_head_sha256"],
                    "active_task_id": old_active_task_id,
                    "candidate_absent": True,
                    "pending_hil": False,
                },
                "old_active_task_id": old_active_task_id,
                "replacement_task_id": replacement_task_id,
                "approved_review_gate_id": review_task_id,
                "superseded_task_ids": superseded_task_ids,
                "started_at": now,
                "phase": "PREPARED",
                "phase_updated_at": now,
            }
            self._write_plan_normalization_journal(
                project_id,
                transition_id,
                journal,
            )

        def advance(phase: str, **details: Any) -> None:
            journal["phase"] = phase
            journal["phase_updated_at"] = utc_now()
            journal.update(details)
            self._write_plan_normalization_journal(
                project_id,
                transition_id,
                journal,
            )

        if journal["phase"] == "PREPARED":
            appended = self.store.plan_tasks(
                project_id,
                tasks=tasks,
                planned_by=planned_by,
                plan_id=plan_id,
                normalization_transition_id=transition_id,
            )
            advance(
                "PLAN_APPENDED",
                appended_plan_sha256=appended["canonical_plan_projection"][
                    "projection_sha256"
                ],
                appended_event_head_sha256=appended["event_head_sha256"],
            )

        if journal["phase"] == "PLAN_APPENDED":
            activated = self.store.activate_plan_normalization(
                project_id,
                plan_id=plan_id,
                review_task_id=review_task_id,
                replacement_task_id=replacement_task_id,
                session_id=session_id,
                runtime_task_id=replacement_task_id,
                approved_by=planned_by,
                approval_receipt_sha256=approval_receipt_sha256,
            )
            advance(
                "PLAN_ACTIVATED",
                activated_plan_sha256=activated["canonical_plan_projection"][
                    "projection_sha256"
                ],
                activated_event_head_sha256=activated["event_head_sha256"],
            )

        if journal["phase"] == "PLAN_ACTIVATED":
            session = self.load(project_id, session_id)
            backlog = self.store.backlog_status(project_id)
            replacement = next(
                row for row in backlog["tasks"] if row["task_id"] == replacement_task_id
            )
            already_rebound = (
                session.metadata.get("active_backlog_task_id") == replacement_task_id
                and isinstance(session.task, dict)
                and session.task.get("task_id") == replacement_task_id
            )
            if not already_rebound:
                require(
                    session.metadata.get("active_backlog_task_id") == old_active_task_id
                    and isinstance(session.task, dict),
                    "PLAN_NORMALIZATION_SESSION_REBIND_MISMATCH",
                    "Crash recovery found neither the old nor exact replacement session binding.",
                    status="MISMATCH",
                    active_backlog_task_id=session.metadata.get(
                        "active_backlog_task_id"
                    ),
                    runtime_task_id=(
                        session.task.get("task_id")
                        if isinstance(session.task, dict)
                        else None
                    ),
                )
                prior_task = cast(dict[str, Any], session.task)
                replacement_contract = classify_task(
                    task_id=replacement_task_id,
                    task_class=str(replacement["task_class"]),
                    requested_outcome=str(replacement["requested_outcome"]),
                    permitted_paths=cast(list[str], replacement["permitted_paths"]),
                    permitted_tools=cast(list[str], replacement["permitted_tools"]),
                    acceptance_checks=cast(list[str], replacement["acceptance_checks"]),
                    stop_condition=str(replacement["stop_condition"]),
                ).as_dict()
                rebound_at = utc_now()
                run_id = f"run_norm_{request_sha256[:24].lower()}"
                rebind_body = {
                    "schema": "evidence-lane.plan-normalization-rebind.v1",
                    "transition_id": transition_id,
                    "plan_id": plan_id,
                    "session_id": session_id,
                    "old_backlog_task_id": old_active_task_id,
                    "old_runtime_task_id": prior_task.get("task_id"),
                    "replacement_backlog_task_id": replacement_task_id,
                    "replacement_runtime_task_id": replacement_task_id,
                    "approval_receipt_sha256": approval_receipt_sha256,
                    "candidate_created": False,
                    "pending_hil": False,
                    "pointer_moved": False,
                    "rebound_at": rebound_at,
                }
                rebind_receipt = {
                    **rebind_body,
                    "receipt_sha256": sha256_bytes(canonical_json_bytes(rebind_body)),
                }
                session.metadata.setdefault("plan_normalization_rebinds", []).append(
                    rebind_receipt
                )
                session.metadata["active_backlog_task_id"] = replacement_task_id
                session.metadata["run_id"] = run_id
                session.metadata["source_update_confirmed"] = False
                session.task = replacement_contract
                active_mode_binding = session.metadata.get("active_mode_binding")
                if isinstance(active_mode_binding, dict) and isinstance(
                    active_mode_binding.get("mode_governance"), dict
                ):
                    governance = cast(
                        dict[str, Any], active_mode_binding["mode_governance"]
                    )
                    validate_mode_governance_selection(governance)
                    mode_body = {
                        "schema": "evidence-lane.task-mode-binding.v1",
                        "task_id": replacement_task_id,
                        "selected_mode_ids": list(
                            active_mode_binding["selected_mode_ids"]
                        ),
                        "mode_intersection": active_mode_binding["mode_intersection"],
                        "canonical_lanes": list(active_mode_binding["canonical_lanes"]),
                        "selection_source": active_mode_binding["selection_source"],
                        "mode_governance": governance,
                        "selection_receipt_sha256": active_mode_binding[
                            "binding_receipt_sha256"
                        ],
                        "bound_at_task_classification": rebound_at,
                        "lifecycle_effect": "NONE",
                        "candidate_created": False,
                        "pointer_moved": False,
                        "hil_approval_inferred": False,
                    }
                    session.metadata["task_mode_binding"] = {
                        **mode_body,
                        "binding_receipt_sha256": sha256_bytes(
                            canonical_json_bytes(mode_body)
                        ),
                    }
                self._save(session)
                ChatLineage(self._lineage_path(project_id, session_id)).append(
                    event_type="plan.normalization.rebound",
                    visible_payload=rebind_receipt,
                    occurred_at=rebound_at,
                    session_id=session_id,
                    task_id=replacement_task_id,
                    run_id=run_id,
                    event_id=f"{transition_id}__session_rebound",
                )
            advance(
                "SESSION_REBOUND",
                rebound_session_sha256=self.session_snapshot_sha256(
                    self.load(project_id, session_id)
                ),
            )

        backlog = self.store.backlog_status(project_id)
        session = self.load(project_id, session_id)
        pointer_sha256 = self.pointer_snapshot_sha256(project_id)
        expected_total = transition.get("expected_result_task_count")
        if expected_total is not None:
            require(
                isinstance(expected_total, int)
                and len(backlog["tasks"]) == expected_total,
                "PLAN_NORMALIZATION_RESULT_COUNT_MISMATCH",
                "The normalized Plan row count differs from the approved result.",
                status="MISMATCH",
                expected=expected_total,
                current=len(backlog["tasks"]),
            )
        tasks_after = {str(row["task_id"]): row for row in backlog["tasks"]}
        failed_checks = {
            "sole_active_replacement": [
                str(row["task_id"]) for row in backlog["active"]
            ]
            == [replacement_task_id],
            "review_done": tasks_after.get(review_task_id, {}).get("status") == "DONE",
            "all_old_rows_superseded": all(
                tasks_after.get(task_id, {}).get("status") == "SUPERSEDED"
                for task_id in superseded_task_ids
            ),
            "session_backlog_binding": session.metadata.get("active_backlog_task_id")
            == replacement_task_id,
            "session_runtime_binding": isinstance(session.task, dict)
            and session.task.get("task_id") == replacement_task_id,
            "candidate_absent": session.candidate_id is None,
            "pending_hil_false": not bool(session.metadata.get("pending_hil")),
            "pointer_unchanged": pointer_sha256 == expected_pointer_sha256,
            "physically_final": str(
                backlog["tasks"][-1].get("panel_role") or ""
            ).upper()
            == "PHYSICALLY_FINAL_HIL",
        }
        require(
            all(failed_checks.values()),
            "PLAN_NORMALIZATION_COMMIT_VERIFICATION_FAILED",
            "The journal cannot commit until Plan, session, pointer, and final-HIL invariants all pass.",
            status="FAIL",
            failed_checks=sorted(
                key for key, passed in failed_checks.items() if not passed
            ),
        )
        final_body = {
            "canonical_plan_sha256": backlog["canonical_plan_projection"][
                "projection_sha256"
            ],
            "goal_projection_sha256": backlog["goal_projection"]["projection_sha256"],
            "history_projection_sha256": backlog["history_projection"][
                "projection_sha256"
            ],
            "plan_runtime_sqlite_sha256": backlog["plan_runtime_projection"][
                "sqlite_sha256"
            ],
            "plan_runtime_projection_content_sha256": backlog[
                "plan_runtime_projection"
            ]["projection_content_sha256"],
            "event_count": backlog["event_count"],
            "event_head_sha256": backlog["event_head_sha256"],
            "session_sha256": self.session_snapshot_sha256(session),
            "pointer_sha256": pointer_sha256,
            "task_count": len(backlog["tasks"]),
            "counts": backlog["counts"],
            "active_task_id": replacement_task_id,
            "physically_final_task_id": backlog["tasks"][-1]["task_id"],
            "candidate_created": False,
            "pending_hil": False,
            "pointer_moved": False,
        }
        if journal["phase"] != "COMMITTED":
            advance(
                "COMMITTED",
                status="PASS",
                committed_at=utc_now(),
                result=final_body,
                result_sha256=sha256_bytes(canonical_json_bytes(final_body)),
            )
            idempotent_replay = False
        else:
            require(
                journal.get("result") == final_body,
                "PLAN_NORMALIZATION_COMMITTED_STATE_DRIFT",
                "The committed normalization no longer matches its sealed result.",
                status="MISMATCH",
                transition_id=transition_id,
            )
            idempotent_replay = True
        return {
            **backlog,
            "normalization_transition": {
                **final_body,
                "status": "PASS",
                "schema": _PLAN_NORMALIZATION_SCHEMA,
                "transition_id": transition_id,
                "plan_id": plan_id,
                "approval_receipt_sha256": approval_receipt_sha256,
                "journal_phase": journal["phase"],
                "journal_path": str(
                    self._plan_normalization_path(project_id, transition_id)
                ),
                "request_sha256": request_sha256,
                "result_sha256": journal["result_sha256"],
                "idempotent_replay": idempotent_replay,
                "crash_recoverable": True,
                "writes_on_precondition_mismatch": 0,
            },
        }

    def rebind_active_task_contract(
        self,
        project_id: str,
        *,
        rebound_by: str,
        active_contract_rebind: dict[str, Any],
    ) -> dict[str, Any]:
        """Amend one active Plan contract and rebind its existing runtime task.

        Plan and session state are separate durable authorities. The journal makes
        their in-place amendment recoverable without replacing the Plan row,
        runtime task, governed session, host task, Goal, candidate, or pointer.
        """

        require(
            isinstance(active_contract_rebind, dict),
            "ACTIVE_CONTRACT_REBIND_CONTRACT_INVALID",
            "The active-contract rebind must be one structured contract.",
            status="BLOCKED",
        )
        contract = dict(active_contract_rebind)
        rebind_id = str(contract.get("rebind_id") or "").strip()
        session_id = str(contract.get("session_id") or "").strip()
        active_task_id = str(contract.get("active_task_id") or "").strip()
        runtime_task_id = str(contract.get("expected_runtime_task_id") or "").strip()
        host_task_id = str(contract.get("host_task_id") or "").strip()
        actor = str(rebound_by or "").strip()
        approval_receipt_path = str(contract.get("approval_receipt_path") or "").strip()
        for field, value in (
            ("rebind_id", rebind_id),
            ("session_id", session_id),
            ("active_task_id", active_task_id),
            ("expected_runtime_task_id", runtime_task_id),
            ("host_task_id", host_task_id),
            ("rebound_by", actor),
            ("approval_receipt_path", approval_receipt_path),
        ):
            require(
                bool(value),
                "ACTIVE_CONTRACT_REBIND_CONTRACT_INVALID",
                "The active-contract rebind is missing one exact identity.",
                status="BLOCKED",
                field=field,
            )
        self._active_contract_rebind_path(project_id, rebind_id)
        require(
            session_id.startswith("session_")
            and session_id.replace("_", "").isalnum()
            and len(active_task_id) <= 96
            and all(character in _SAFE_ID_CHARACTERS for character in active_task_id)
            and len(runtime_task_id) <= 96
            and all(character in _SAFE_ID_CHARACTERS for character in runtime_task_id)
            and len(host_task_id) <= 96
            and all(character in _SAFE_ID_CHARACTERS for character in host_task_id),
            "ACTIVE_CONTRACT_REBIND_ID_INVALID",
            "The governed session, Plan, runtime-task, or host-task identity is invalid.",
            status="BLOCKED",
        )
        require(
            contract.get("expected_candidate_absent") is True
            and contract.get("expected_pending_hil") is False,
            "ACTIVE_CONTRACT_REBIND_UNACCEPTED_STATE_EXPECTATION_REQUIRED",
            "The rebind must explicitly expect no candidate and pending_hil=false.",
            status="BLOCKED",
        )
        expected_hashes = {
            field: _require_active_contract_sha256(contract.get(field), field=field)
            for field in (
                "expected_backlog_sha256",
                "expected_canonical_plan_sha256",
                "expected_executable_projection_sha256",
                "expected_session_sha256",
                "expected_pointer_sha256",
                "approval_receipt_sha256",
            )
        }
        replacement_supplied = contract.get("replacement_contract")
        require(
            isinstance(replacement_supplied, dict),
            "ACTIVE_CONTRACT_REBIND_REPLACEMENT_INVALID",
            "The rebind requires one structured replacement task contract.",
            status="BLOCKED",
        )
        replacement_plan_contract = self.store._normalize_plan_task_rows(
            [
                {
                    **cast(dict[str, Any], replacement_supplied),
                    "task_id": active_task_id,
                }
            ]
        )[0]
        replacement_runtime_contract = classify_task(
            task_id=runtime_task_id,
            task_class=str(replacement_plan_contract["task_class"]),
            requested_outcome=str(replacement_plan_contract["requested_outcome"]),
            permitted_paths=cast(
                list[str], replacement_plan_contract["permitted_paths"]
            ),
            permitted_tools=cast(
                list[str], replacement_plan_contract["permitted_tools"]
            ),
            acceptance_checks=cast(
                list[str], replacement_plan_contract["acceptance_checks"]
            ),
            stop_condition=str(replacement_plan_contract["stop_condition"]),
        )

        repository_root = Path(self.store.config(project_id).repository_path).resolve()
        supplied_approval_path = Path(approval_receipt_path)
        require(
            not supplied_approval_path.is_absolute(),
            "ACTIVE_CONTRACT_REBIND_APPROVAL_PATH_INVALID",
            "The approval receipt path must be repository-relative.",
            status="BLOCKED",
            path=approval_receipt_path,
        )
        approval_path = (repository_root / supplied_approval_path).resolve()
        try:
            approval_path.relative_to(repository_root)
            approval_inside_repository = True
        except ValueError:
            approval_inside_repository = False
        require(
            approval_inside_repository and approval_path.is_file(),
            "ACTIVE_CONTRACT_REBIND_APPROVAL_PATH_INVALID",
            "The exact approval receipt must be a file inside the registered repository.",
            status="MISMATCH",
            path=approval_receipt_path,
        )
        observed_approval_sha256 = sha256_file(approval_path)
        require(
            observed_approval_sha256 == expected_hashes["approval_receipt_sha256"],
            "ACTIVE_CONTRACT_REBIND_APPROVAL_HASH_MISMATCH",
            "The visible user-authority receipt bytes differ from the sealed hash.",
            status="MISMATCH",
            expected=expected_hashes["approval_receipt_sha256"],
            observed=observed_approval_sha256,
            writes_performed=False,
        )
        try:
            approval = json.loads(approval_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceLaneError(
                "ACTIVE_CONTRACT_REBIND_APPROVAL_INVALID",
                "The visible user-authority receipt is not valid JSON.",
                status="MISMATCH",
                details={"error": type(exc).__name__},
            ) from exc
        identity_invariants = approval.get("identity_invariants")
        forbidden_effects = approval.get("not_authorized")
        forbidden_text = " ".join(
            str(value).lower()
            for value in (
                forbidden_effects if isinstance(forbidden_effects, list) else []
            )
        )
        require(
            approval.get("schema") == "evidence-lane.visible-user-authority-receipt.v1"
            and approval.get("authority_kind")
            == "HOST_PLAN_EXECUTION_AND_CONTRACT_CORRECTION"
            and approval.get("project_id") == project_id
            and approval.get("session_id") == session_id
            and approval.get("host_task_id") == host_task_id
            and isinstance(identity_invariants, dict)
            and all(
                cast(dict[str, Any], identity_invariants).get(field) is True
                for field in (
                    "same_project",
                    "same_session",
                    "same_host_task",
                    "same_active_plan_row",
                    "same_dirty_worktree",
                )
            )
            and all(
                marker in forbidden_text
                for marker in (
                    "hil",
                    "candidate",
                    "pointer",
                    "goal completion",
                    "git",
                    "plugin install",
                )
            ),
            "ACTIVE_CONTRACT_REBIND_APPROVAL_INVALID",
            "The receipt does not bind the exact invoking-task correction and exclusions.",
            status="MISMATCH",
            writes_performed=False,
        )

        request_body = {
            "project_id": project_id,
            "rebind_id": rebind_id,
            "session_id": session_id,
            "active_task_id": active_task_id,
            "runtime_task_id": runtime_task_id,
            "host_task_id": host_task_id,
            "rebound_by": actor,
            "approval_receipt_path": supplied_approval_path.as_posix(),
            "approval_receipt_sha256": observed_approval_sha256,
            "expected_hashes": expected_hashes,
            "replacement_contract": replacement_plan_contract,
        }
        request_sha256 = sha256_bytes(canonical_json_bytes(request_body))
        journal = self._load_active_contract_rebind_journal(project_id, rebind_id)
        if journal is not None:
            require(
                journal.get("request_sha256") == request_sha256,
                "ACTIVE_CONTRACT_REBIND_REPLAY_CONFLICT",
                "The rebind ID already binds a different request.",
                status="BLOCKED",
                rebind_id=rebind_id,
            )
        else:
            raw_backlog = self.store._load_backlog(project_id)
            backlog = self.store.backlog_status(project_id)
            session = self.load(project_id, session_id)
            pointer = self.store.pointer(project_id)
            current_hashes = {
                "backlog_sha256": sha256_bytes(canonical_json_bytes(raw_backlog)),
                "canonical_plan_sha256": str(
                    backlog["canonical_plan_projection"]["projection_sha256"]
                ),
                "executable_projection_sha256": str(
                    backlog["goal_projection"]["projection_sha256"]
                ),
                "session_sha256": self.session_snapshot_sha256(session),
                "pointer_sha256": sha256_bytes(canonical_json_bytes(pointer.as_dict())),
            }
            mismatches = {
                key: {"expected": expected, "current": current_hashes[key]}
                for key, expected in (
                    (
                        "backlog_sha256",
                        expected_hashes["expected_backlog_sha256"],
                    ),
                    (
                        "canonical_plan_sha256",
                        expected_hashes["expected_canonical_plan_sha256"],
                    ),
                    (
                        "executable_projection_sha256",
                        expected_hashes["expected_executable_projection_sha256"],
                    ),
                    (
                        "session_sha256",
                        expected_hashes["expected_session_sha256"],
                    ),
                    (
                        "pointer_sha256",
                        expected_hashes["expected_pointer_sha256"],
                    ),
                )
                if expected != current_hashes[key]
            }
            require(
                not mismatches,
                "ACTIVE_CONTRACT_REBIND_PRECONDITION_MISMATCH",
                "Plan, session, and pointer authorities must match before any rebind write.",
                status="MISMATCH",
                mismatches=mismatches,
                writes_performed=False,
            )
            active_ids = [str(row["task_id"]) for row in backlog["active"]]
            require(
                active_ids == [active_task_id]
                and session.metadata.get("active_backlog_task_id") == active_task_id
                and isinstance(session.task, dict)
                and session.task.get("task_id") == runtime_task_id
                and session.metadata.get("current_host_session_id") == host_task_id
                and session.state == SessionState.TASK_CLASSIFIED,
                "ACTIVE_CONTRACT_REBIND_ACTIVE_BINDING_MISMATCH",
                "The exact active Plan, runtime task, governed session, and invoking-task binding must agree.",
                status="MISMATCH",
                active_task_ids=active_ids,
                session_backlog_task_id=session.metadata.get("active_backlog_task_id"),
                session_runtime_task_id=(
                    session.task.get("task_id")
                    if isinstance(session.task, dict)
                    else None
                ),
                session_host_task_id=session.metadata.get("current_host_session_id"),
                writes_performed=False,
            )
            require(
                session.candidate_id is None
                and not bool(session.metadata.get("pending_hil"))
                and not isinstance(session.metadata.get("pending_task"), dict),
                "ACTIVE_CONTRACT_REBIND_CANDIDATE_OR_HIL_PRESENT",
                "A contract rebind cannot run with a candidate or pending HIL state.",
                status="BLOCKED",
                candidate_id=session.candidate_id,
                pending_hil=bool(session.metadata.get("pending_hil")),
                writes_performed=False,
            )
            require(
                pointer.generation == session.accepted_pointer_generation
                and pointer.accepted_pv == session.accepted_pv
                and cast(dict[str, Any], identity_invariants).get("accepted_pv")
                == pointer.accepted_pv
                and cast(dict[str, Any], identity_invariants).get("pointer_generation")
                == pointer.generation,
                "ACTIVE_CONTRACT_REBIND_SESSION_POINTER_STALE",
                "The session, pointer, and visible authority receipt do not share one accepted authority.",
                status="STALE",
                writes_performed=False,
            )
            now = utc_now()
            journal = {
                "schema": _ACTIVE_CONTRACT_REBIND_SCHEMA,
                "status": "IN_PROGRESS",
                "project_id": project_id,
                "rebind_id": rebind_id,
                "session_id": session_id,
                "active_task_id": active_task_id,
                "runtime_task_id": runtime_task_id,
                "host_task_id": host_task_id,
                "request_sha256": request_sha256,
                "approval_receipt_sha256": observed_approval_sha256,
                "baseline": {
                    **current_hashes,
                    "task_count": len(backlog["tasks"]),
                    "event_count": backlog["event_count"],
                    "event_head_sha256": backlog["event_head_sha256"],
                    "physical_final_task_id": backlog["tasks"][-1]["task_id"],
                    "prior_runtime_contract_sha256": sha256_bytes(
                        canonical_json_bytes(cast(dict[str, Any], session.task))
                    ),
                    "candidate_absent": True,
                    "pending_hil": False,
                },
                "replacement_plan_contract": replacement_plan_contract,
                "replacement_runtime_contract": (
                    replacement_runtime_contract.as_dict()
                ),
                "started_at": now,
                "phase": "PREPARED",
                "phase_updated_at": now,
            }
            self._write_active_contract_rebind_journal(project_id, rebind_id, journal)

        def advance(phase: str, **details: Any) -> None:
            journal["phase"] = phase
            journal["phase_updated_at"] = utc_now()
            journal.update(details)
            self._write_active_contract_rebind_journal(project_id, rebind_id, journal)

        if journal["phase"] == "PREPARED":
            amended = self.store.amend_active_task_contract(
                project_id,
                amendment_id=rebind_id,
                session_id=session_id,
                active_task_id=active_task_id,
                replacement_contract=replacement_plan_contract,
                amended_by=actor,
                approval_receipt_sha256=observed_approval_sha256,
                expected_backlog_sha256=expected_hashes["expected_backlog_sha256"],
                expected_canonical_plan_sha256=expected_hashes[
                    "expected_canonical_plan_sha256"
                ],
                expected_executable_projection_sha256=expected_hashes[
                    "expected_executable_projection_sha256"
                ],
                request_sha256=request_sha256,
            )
            amendment_receipt = cast(
                dict[str, Any], amended["active_contract_amendment_receipt"]
            )
            advance(
                "PLAN_AMENDED",
                amendment_receipt_sha256=amendment_receipt["receipt_sha256"],
                amended_canonical_plan_sha256=amended["canonical_plan_projection"][
                    "projection_sha256"
                ],
                amended_executable_projection_sha256=amended["goal_projection"][
                    "projection_sha256"
                ],
                amended_event_head_sha256=amended["event_head_sha256"],
            )

        if journal["phase"] == "PLAN_AMENDED":
            session = self.load(project_id, session_id)
            pointer = self.store.pointer(project_id)
            rebinds = session.metadata.setdefault("active_contract_rebinds", [])
            require(
                isinstance(rebinds, list),
                "ACTIVE_CONTRACT_REBIND_SESSION_HISTORY_INVALID",
                "The session contract-rebind history must be append-only data.",
                status="MISMATCH",
            )
            existing = next(
                (
                    item
                    for item in cast(list[Any], rebinds)
                    if isinstance(item, dict) and item.get("rebind_id") == rebind_id
                ),
                None,
            )
            expected_runtime_payload = replacement_runtime_contract.as_dict()
            if existing is not None:
                require(
                    existing.get("request_sha256") == request_sha256
                    and session.task == expected_runtime_payload
                    and session.metadata.get("active_backlog_task_id") == active_task_id
                    and session.metadata.get("current_host_session_id") == host_task_id,
                    "ACTIVE_CONTRACT_REBIND_SESSION_REPLAY_CONFLICT",
                    "Crash recovery found a different session rebind under the same identity.",
                    status="MISMATCH",
                )
                rebind_receipt = cast(dict[str, Any], existing)
            else:
                require(
                    session.metadata.get("active_backlog_task_id") == active_task_id
                    and isinstance(session.task, dict)
                    and session.task.get("task_id") == runtime_task_id
                    and session.metadata.get("current_host_session_id") == host_task_id,
                    "ACTIVE_CONTRACT_REBIND_SESSION_REBIND_MISMATCH",
                    "Crash recovery found neither the prior nor exact rebound session contract.",
                    status="MISMATCH",
                )
                prior_runtime_contract_sha256 = sha256_bytes(
                    canonical_json_bytes(cast(dict[str, Any], session.task))
                )
                prior_active_rebind = session.metadata.get(
                    "active_contract_rebind_receipt"
                )
                prior_active_rebind_receipt_sha256 = (
                    str(
                        cast(dict[str, Any], prior_active_rebind).get("receipt_sha256")
                        or ""
                    )
                    if isinstance(prior_active_rebind, dict)
                    else None
                )
                direct_entry = session.metadata.get("direct_forced_same_worktree_entry")
                direct_entry_receipt_sha256 = (
                    str(cast(dict[str, Any], direct_entry).get("receipt_sha256") or "")
                    if isinstance(direct_entry, dict)
                    else None
                )
                require(
                    prior_runtime_contract_sha256
                    == journal["baseline"]["prior_runtime_contract_sha256"],
                    "ACTIVE_CONTRACT_REBIND_PRIOR_RUNTIME_DRIFT",
                    "The runtime task contract changed after the transaction prepared.",
                    status="MISMATCH",
                )
                rebound_at = utc_now()
                rebind_body = {
                    "schema": "evidence-lane.active-contract-session-rebind.v1",
                    "status": "PASS",
                    "project_id": project_id,
                    "session_id": session_id,
                    "rebind_id": rebind_id,
                    "request_sha256": request_sha256,
                    "approval_receipt_sha256": observed_approval_sha256,
                    "active_plan_task_id": active_task_id,
                    "runtime_task_id": runtime_task_id,
                    "host_task_id": host_task_id,
                    "prior_runtime_contract_sha256": (prior_runtime_contract_sha256),
                    "replacement_runtime_contract_sha256": sha256_bytes(
                        canonical_json_bytes(expected_runtime_payload)
                    ),
                    "runtime_task_identity_preserved": True,
                    "active_plan_row_identity_preserved": True,
                    "governed_session_identity_preserved": True,
                    "host_task_identity_preserved": True,
                    "authority_route": "PV_PLAN_TASKS_ACTIVE_CONTRACT_REBIND",
                    "entry_authority_receipt_sha256": (
                        direct_entry_receipt_sha256 or None
                    ),
                    "prior_active_contract_rebind_receipt_sha256": (
                        prior_active_rebind_receipt_sha256 or None
                    ),
                    "task_binding_contract": {
                        "manager_scope": "SHARED_MULTI_PROJECT_MULTI_TASK",
                        "registry_mutability": "MUTABLE_APPEND_OR_REFRESH",
                        "invocation_binding_scope": "EXACT_CALLING_TASK",
                        "reentry_target": host_task_id,
                        "installer_helper": "SEPARATE_COMPONENT",
                    },
                    "candidate_created": False,
                    "pending_hil": False,
                    "pointer_moved": False,
                    "goal_completion_mutated": False,
                    "git_executed": False,
                    "install_executed": False,
                    "helper_launched": False,
                    "tunnel_launched": False,
                    "rebound_at": rebound_at,
                }
                rebind_receipt = {
                    **rebind_body,
                    "receipt_sha256": sha256_bytes(canonical_json_bytes(rebind_body)),
                }
                session.task = expected_runtime_payload
                session.metadata["active_backlog_task_id"] = active_task_id
                session.metadata["active_contract_rebind_receipt"] = rebind_receipt
                cast(list[dict[str, Any]], rebinds).append(rebind_receipt)
                self._seal_task_classification_binding(
                    project_id,
                    session_id,
                    session=session,
                    task=replacement_runtime_contract,
                    pointer=pointer,
                    target_backlog_task_id=active_task_id,
                    prior_executable_task_id=active_task_id,
                )
                self._save(session)
            ChatLineage(self._lineage_path(project_id, session_id)).append(
                event_type="task.active_contract.rebound",
                visible_payload=rebind_receipt,
                occurred_at=str(rebind_receipt["rebound_at"]),
                session_id=session_id,
                task_id=runtime_task_id,
                run_id=str(session.metadata.get("run_id") or "") or None,
                event_id=f"{rebind_id}__session_rebound",
            )
            advance(
                "SESSION_REBOUND",
                session_rebind_receipt_sha256=rebind_receipt["receipt_sha256"],
                rebound_session_sha256=self.session_snapshot_sha256(
                    self.load(project_id, session_id)
                ),
            )

        runtime_status = self.store.plan_runtime_status(project_id)
        plan_runtime_refresh: dict[str, Any] | None = None
        if runtime_status.get("status") != "PASS":
            expected_projection_content_sha256 = str(
                runtime_status.get("expected_projection_content_sha256") or ""
            )
            refresh_id = (
                f"{rebind_id}__{expected_projection_content_sha256[:24].lower()}"
            )
            plan_runtime_refresh = self.store.refresh_plan_runtime_projection(
                project_id,
                refresh_id=refresh_id,
                expected_backlog_sha256=sha256_bytes(
                    canonical_json_bytes(self.store._load_backlog(project_id))
                ),
                expected_projection_content_sha256=(expected_projection_content_sha256),
                refreshed_by=actor,
                reason="ACTIVE_CONTRACT_REBIND_DERIVED_INDEX_PARITY",
            )

        backlog = self.store.backlog_status(project_id)
        session = self.load(project_id, session_id)
        pointer_sha256 = self.pointer_snapshot_sha256(project_id)
        task_by_id = {str(row["task_id"]): row for row in backlog["tasks"]}
        active_plan_task = task_by_id.get(active_task_id)
        plan_contract_after = (
            {field: active_plan_task.get(field) for field in replacement_plan_contract}
            if isinstance(active_plan_task, dict)
            else None
        )
        amendments = (
            active_plan_task.get("task_contract_amendments", [])
            if isinstance(active_plan_task, dict)
            else []
        )
        amendment = next(
            (
                item
                for item in amendments
                if isinstance(item, dict) and item.get("amendment_id") == rebind_id
            ),
            None,
        )
        classification_binding = session.metadata.get("task_classification_binding")
        capture_route = CaptureRouteAuthority(
            self.store.project_root(project_id)
        ).status()
        session_rebinds = session.metadata.get("active_contract_rebinds")
        session_rebind = (
            next(
                (
                    item
                    for item in session_rebinds
                    if isinstance(item, dict) and item.get("rebind_id") == rebind_id
                ),
                None,
            )
            if isinstance(session_rebinds, list)
            else None
        )
        checks = {
            "sole_active_row_preserved": [
                str(row["task_id"]) for row in backlog["active"]
            ]
            == [active_task_id],
            "active_plan_contract_replaced": (
                plan_contract_after == replacement_plan_contract
                and isinstance(active_plan_task, dict)
                and active_plan_task.get("current_contract_authority")
                == "ACTIVE_CONTRACT_REBIND"
            ),
            "append_only_plan_amendment_present": (
                isinstance(amendment, dict)
                and amendment.get("request_sha256") == request_sha256
            ),
            "runtime_task_identity_preserved": (
                isinstance(session.task, dict)
                and session.task == replacement_runtime_contract.as_dict()
                and session.task.get("task_id") == runtime_task_id
            ),
            "session_plan_binding_preserved": session.metadata.get(
                "active_backlog_task_id"
            )
            == active_task_id,
            "host_task_binding_preserved": session.metadata.get(
                "current_host_session_id"
            )
            == host_task_id,
            "classification_binding_resealed": (
                isinstance(classification_binding, dict)
                and classification_binding.get("runtime_task_id") == runtime_task_id
                and classification_binding.get("task_class")
                == replacement_runtime_contract.task_class.value
                and classification_binding.get("authority_boundary", {}).get(
                    "write_boundary"
                )
                == replacement_runtime_contract.write_boundary
            ),
            "session_rebind_receipt_present": (
                isinstance(session_rebind, dict)
                and session_rebind.get("request_sha256") == request_sha256
            ),
            "capture_route_bound": (
                capture_route.get("project_id") == project_id
                and capture_route.get("capture_route")
                in {"ENV_BUILDER_SPARSE", "GOVERNED_PROJECT_FULL"}
            ),
            "candidate_absent": session.candidate_id is None,
            "pending_hil_false": not bool(session.metadata.get("pending_hil")),
            "pointer_unchanged": pointer_sha256
            == expected_hashes["expected_pointer_sha256"],
            "task_count_unchanged": len(backlog["tasks"])
            == journal["baseline"]["task_count"],
            "physical_final_task_preserved": backlog["tasks"][-1]["task_id"]
            == journal["baseline"]["physical_final_task_id"],
        }
        require(
            all(checks.values()),
            "ACTIVE_CONTRACT_REBIND_COMMIT_VERIFICATION_FAILED",
            "The rebind cannot commit until Plan, session, invoking-task, and pointer invariants pass.",
            status="FAIL",
            failed_checks=sorted(key for key, passed in checks.items() if not passed),
        )
        final_body = {
            "canonical_plan_sha256": backlog["canonical_plan_projection"][
                "projection_sha256"
            ],
            "executable_projection_sha256": backlog["goal_projection"][
                "projection_sha256"
            ],
            "history_projection_sha256": backlog["history_projection"][
                "projection_sha256"
            ],
            "plan_runtime_sqlite_sha256": backlog["plan_runtime_projection"][
                "sqlite_sha256"
            ],
            "event_count": backlog["event_count"],
            "event_head_sha256": backlog["event_head_sha256"],
            "session_sha256": self.session_snapshot_sha256(session),
            "pointer_sha256": pointer_sha256,
            "task_count": len(backlog["tasks"]),
            "counts": backlog["counts"],
            "active_task_id": active_task_id,
            "runtime_task_id": runtime_task_id,
            "host_task_id": host_task_id,
            "capture_route": capture_route["capture_route"],
            "capture_route_binding_sha256": capture_route["binding_sha256"],
            "physically_final_task_id": backlog["tasks"][-1]["task_id"],
            "candidate_created": False,
            "pending_hil": False,
            "pointer_moved": False,
            "goal_completion_mutated": False,
            "git_executed": False,
            "install_executed": False,
            "helper_launched": False,
            "tunnel_launched": False,
        }
        if journal["phase"] != "COMMITTED":
            advance(
                "COMMITTED",
                status="PASS",
                committed_at=utc_now(),
                result=final_body,
                result_sha256=sha256_bytes(canonical_json_bytes(final_body)),
            )
            idempotent_replay = False
        else:
            sealed_result = dict(cast(dict[str, Any], journal.get("result")))
            current_result = dict(final_body)
            sealed_sqlite_sha256 = sealed_result.pop("plan_runtime_sqlite_sha256", None)
            current_sqlite_sha256 = current_result.pop(
                "plan_runtime_sqlite_sha256", None
            )
            require(
                sealed_result == current_result
                and (
                    sealed_sqlite_sha256 == current_sqlite_sha256
                    or (
                        isinstance(plan_runtime_refresh, dict)
                        and plan_runtime_refresh.get("status") == "PASS"
                        and plan_runtime_refresh.get("after_sqlite_sha256")
                        == current_sqlite_sha256
                    )
                ),
                "ACTIVE_CONTRACT_REBIND_COMMITTED_STATE_DRIFT",
                "The committed active-contract rebind no longer matches its sealed result.",
                status="MISMATCH",
                rebind_id=rebind_id,
            )
            idempotent_replay = True
        return {
            **backlog,
            "active_contract_rebind": {
                **final_body,
                "schema": _ACTIVE_CONTRACT_REBIND_SCHEMA,
                "status": "PASS",
                "rebind_id": rebind_id,
                "request_sha256": request_sha256,
                "approval_receipt_sha256": observed_approval_sha256,
                "journal_phase": journal["phase"],
                "journal_path": str(
                    self._active_contract_rebind_path(project_id, rebind_id)
                ),
                "result_sha256": journal["result_sha256"],
                "current_result_sha256": sha256_bytes(canonical_json_bytes(final_body)),
                "idempotent_replay": idempotent_replay,
                "crash_recoverable": True,
                "writes_on_precondition_mismatch": 0,
                "plan_runtime_refresh": plan_runtime_refresh,
            },
        }

    def apply_priority_plan_interruption(
        self,
        project_id: str,
        *,
        planned_by: str,
        plan_id: str,
        atomic_insertion: dict[str, Any],
        priority_interruption: dict[str, Any],
    ) -> dict[str, Any]:
        """Insert one priority Delta, pause the live row, and rebind the session.

        Each phase is idempotent: the atomic-insertion journal owns the new row,
        the ProjectStore swaps ACTIVE/QUEUED under one lock, and the session save
        can be replayed if a process stops between the Plan and session phases.
        """

        contract = dict(priority_interruption)
        interruption_id = str(contract.get("interruption_id") or "").strip()
        session_id = str(contract.get("session_id") or "").strip()
        old_active_task_id = str(contract.get("old_active_task_id") or "").strip()
        replacement_task_id = str(contract.get("replacement_task_id") or "").strip()
        reason = str(contract.get("reason") or "").strip()
        for field, value in (
            ("interruption_id", interruption_id),
            ("session_id", session_id),
            ("old_active_task_id", old_active_task_id),
            ("replacement_task_id", replacement_task_id),
            ("reason", reason),
        ):
            require(
                bool(value),
                "PLAN_PRIORITY_STEER_CONTRACT_INVALID",
                "The priority Plan steer is missing one exact identity or reason.",
                status="BLOCKED",
                field=field,
            )
        require(
            contract.get("expected_candidate_absent") is True
            and contract.get("expected_pending_hil") is False,
            "PLAN_PRIORITY_STEER_UNACCEPTED_STATE_EXPECTATION_REQUIRED",
            "The priority steer must explicitly expect no candidate and no pending HIL.",
            status="BLOCKED",
        )
        insertions = atomic_insertion.get("insertions")
        require(
            isinstance(insertions, list) and len(insertions) == 1,
            "PLAN_PRIORITY_STEER_INSERTION_INVALID",
            "A priority steer requires exactly one bounded insertion group.",
            status="BLOCKED",
        )
        insertion_groups = cast(list[dict[str, Any]], insertions)
        group = insertion_groups[0]
        inserted_tasks = group.get("tasks")
        require(
            group.get("insert_before_task_id") == old_active_task_id
            and isinstance(inserted_tasks, list)
            and len(inserted_tasks) == 1
            and cast(dict[str, Any], inserted_tasks[0]).get("task_id")
            == replacement_task_id,
            "PLAN_PRIORITY_STEER_INSERTION_ORDER_INVALID",
            "The sole inserted correction must be immediately before the live row.",
            status="MISMATCH",
        )
        session = self.load(project_id, session_id)
        pointer = self.store.pointer(project_id)
        backlog_before = self.store.backlog_status(project_id)
        task_ids_before = {str(row["task_id"]): row for row in backlog_before["tasks"]}
        replacement_before = task_ids_before.get(replacement_task_id)
        active_ids_before = [str(row["task_id"]) for row in backlog_before["active"]]
        initial_state = replacement_before is None
        replay_state = (
            isinstance(replacement_before, dict)
            and replacement_before.get("status") == "ACTIVE"
            and task_ids_before.get(old_active_task_id, {}).get("status") == "QUEUED"
        )
        require(
            (
                initial_state
                and active_ids_before == [old_active_task_id]
                and session.metadata.get("active_backlog_task_id") == old_active_task_id
            )
            or (
                replay_state
                and active_ids_before == [replacement_task_id]
                and session.metadata.get("active_backlog_task_id")
                in {old_active_task_id, replacement_task_id}
            ),
            "PLAN_PRIORITY_STEER_ACTIVE_BINDING_MISMATCH",
            "The live Plan and governed session do not match the priority steer boundary.",
            status="MISMATCH",
            active_task_ids=active_ids_before,
            session_backlog_task_id=session.metadata.get("active_backlog_task_id"),
        )
        require(
            session.state == SessionState.TASK_CLASSIFIED
            and session.candidate_id is None
            and not bool(session.metadata.get("pending_hil"))
            and not isinstance(session.metadata.get("pending_task"), dict)
            and pointer.generation == session.accepted_pointer_generation
            and pointer.accepted_pv == session.accepted_pv,
            "PLAN_PRIORITY_STEER_SESSION_BOUNDARY_INVALID",
            "Priority work may interrupt only the exact candidate-free classified session.",
            status="BLOCKED",
            session_state=session.state.value,
            candidate_id=session.candidate_id,
            pending_hil=bool(session.metadata.get("pending_hil")),
        )
        if initial_state:
            appended = self.store.plan_tasks_atomic_insert(
                project_id,
                insertions=cast(list[dict[str, Any]], insertions),
                planned_by=planned_by,
                plan_id=plan_id,
                batch_id=str(atomic_insertion.get("batch_id") or ""),
                research_batch_sha256=str(
                    atomic_insertion.get("research_batch_sha256") or ""
                ),
                expected_backlog_sha256=str(
                    atomic_insertion.get("expected_backlog_sha256") or ""
                ),
                expected_canonical_plan_sha256=str(
                    atomic_insertion.get("expected_canonical_plan_sha256") or ""
                ),
                expected_executable_projection_sha256=str(
                    atomic_insertion.get("expected_executable_projection_sha256") or ""
                ),
                expected_physical_final_task_id=str(
                    atomic_insertion.get("expected_physical_final_task_id") or ""
                ),
            )
        else:
            # The committed insertion journal seals the Plan immediately before
            # the priority swap.  Once ACTIVE/QUEUED changes, generic insertion
            # replay must (correctly) reject the newer backlog hash.  Recover the
            # exact committed receipt here only after proving that the inserted
            # row, its contract, and the journal all bind this interruption.
            replacement_before_row = cast(dict[str, Any], replacement_before)
            exact_batch_id = str(atomic_insertion.get("batch_id") or "").strip()
            journal_path = self.store._plan_atomic_insertion_journal_path(
                project_id,
                exact_batch_id,
            )
            require(
                bool(exact_batch_id) and journal_path.is_file(),
                "PLAN_PRIORITY_STEER_INSERTION_JOURNAL_MISSING",
                "The replayed priority steer is missing its committed insertion journal.",
                status="MISMATCH",
                batch_id=exact_batch_id,
            )
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            receipt = journal.get("receipt")
            normalized_inserted = self.store._normalize_plan_task_rows(
                cast(list[dict[str, Any]], inserted_tasks)
            )[0]
            inserted_contract_matches = all(
                replacement_before_row.get(key) == value
                for key, value in normalized_inserted.items()
            )
            require(
                journal.get("schema")
                == "evidence-lane.plan-atomic-insertion-journal.v1"
                and journal.get("state") == "COMMITTED"
                and journal.get("project_id") == project_id
                and journal.get("batch_id") == exact_batch_id
                and isinstance(receipt, dict)
                and receipt.get("plan_id") == plan_id
                and receipt.get("batch_id") == exact_batch_id
                and receipt.get("research_batch_sha256")
                == atomic_insertion.get("research_batch_sha256")
                and receipt.get("task_ids") == [replacement_task_id]
                and receipt.get("task_count") == 1
                and receipt.get("group_count") == 1
                and receipt.get("physical_final_task_id")
                == atomic_insertion.get("expected_physical_final_task_id")
                and replacement_before_row.get("plan_id") == plan_id
                and inserted_contract_matches,
                "PLAN_PRIORITY_STEER_INSERTION_REPLAY_MISMATCH",
                "The replayed priority steer does not match its sealed insertion contract.",
                status="MISMATCH",
                batch_id=exact_batch_id,
                replacement_task_id=replacement_task_id,
            )
            appended = {
                **backlog_before,
                "atomic_insertion_receipt": {
                    **cast(dict[str, Any], receipt),
                    "idempotent_replay": True,
                    "recovered_prepared_insertion": False,
                },
            }
        reason_sha256 = sha256_bytes(reason.encode("utf-8"))
        activated = self.store.activate_priority_steer(
            project_id,
            old_active_task_id=old_active_task_id,
            replacement_task_id=replacement_task_id,
            session_id=session_id,
            runtime_task_id=replacement_task_id,
            decided_by=planned_by,
            reason_sha256=reason_sha256,
            interruption_id=interruption_id,
        )
        activated_rows = cast(
            list[dict[str, Any]],
            cast(dict[str, Any], activated["goal_projection"])["rows"],
        )
        fixed_batch_task_ids = [str(row["task_id"]) for row in activated_rows[:9]]
        session = self.load(project_id, session_id)
        replacement = next(
            row for row in activated["tasks"] if row["task_id"] == replacement_task_id
        )
        already_rebound = (
            session.metadata.get("active_backlog_task_id") == replacement_task_id
            and isinstance(session.task, dict)
            and session.task.get("task_id") == replacement_task_id
        )
        if not already_rebound:
            require(
                session.metadata.get("active_backlog_task_id") == old_active_task_id
                and isinstance(session.task, dict),
                "PLAN_PRIORITY_STEER_SESSION_REBIND_MISMATCH",
                "The session is bound to neither the paused nor priority Delta.",
                status="MISMATCH",
            )
            prior_task = cast(dict[str, Any], session.task)
            replacement_contract = classify_task(
                task_id=replacement_task_id,
                task_class=str(replacement["task_class"]),
                requested_outcome=str(replacement["requested_outcome"]),
                permitted_paths=cast(list[str], replacement["permitted_paths"]),
                permitted_tools=cast(list[str], replacement["permitted_tools"]),
                acceptance_checks=cast(list[str], replacement["acceptance_checks"]),
                stop_condition=str(replacement["stop_condition"]),
            ).as_dict()
            rebound_at = utc_now()
            run_id = f"run_priority_{sha256_bytes(interruption_id.encode('utf-8'))[:24].lower()}"
            rebind_body = {
                "schema": "evidence-lane.plan-priority-steer-rebind.v1",
                "interruption_id": interruption_id,
                "plan_id": plan_id,
                "session_id": session_id,
                "paused_backlog_task_id": old_active_task_id,
                "paused_runtime_task_id": prior_task.get("task_id"),
                "replacement_backlog_task_id": replacement_task_id,
                "replacement_runtime_task_id": replacement_task_id,
                "reason_sha256": reason_sha256,
                "candidate_created": False,
                "pending_hil": False,
                "pointer_moved": False,
                "rebound_at": rebound_at,
            }
            rebind_receipt = {
                **rebind_body,
                "receipt_sha256": sha256_bytes(canonical_json_bytes(rebind_body)),
            }
            session.metadata.setdefault("priority_steer_rebinds", []).append(
                rebind_receipt
            )
            session.metadata["active_backlog_task_id"] = replacement_task_id
            session.metadata["active_backlog_task_status"] = "ACTIVE"
            session.metadata["run_id"] = run_id
            session.metadata["source_update_confirmed"] = False
            session.task = replacement_contract
            self._save(session)
            ChatLineage(self._lineage_path(project_id, session_id)).append(
                event_type="plan.priority_steer.rebound",
                visible_payload=rebind_receipt,
                occurred_at=rebound_at,
                session_id=session_id,
                task_id=replacement_task_id,
                run_id=run_id,
                event_id=f"{interruption_id}__session_rebound",
            )
        else:
            rebind_receipt = next(
                (
                    cast(dict[str, Any], row)
                    for row in session.metadata.get("priority_steer_rebinds", [])
                    if isinstance(row, dict)
                    and row.get("interruption_id") == interruption_id
                ),
                {},
            )
            require(
                bool(rebind_receipt),
                "PLAN_PRIORITY_STEER_REPLAY_RECEIPT_MISSING",
                "The replayed session binding is missing its priority-steer receipt.",
                status="MISMATCH",
            )
        host_projection = self._prepare_host_plan_rehydration(
            project_id,
            session,
            trigger="ACTIVE_ROW_TRANSITION",
            trigger_event_id=f"{interruption_id}__host_projection",
            host_goal_active=True,
            affected_plan_task_ids=[replacement_task_id, old_active_task_id],
            fixed_window_task_ids=fixed_batch_task_ids,
            reuse_previous_window=False,
        )
        activated["atomic_insertion_receipt"] = appended["atomic_insertion_receipt"]
        activated["priority_steer_rebind"] = rebind_receipt
        activated["host_plan_rehydration"] = host_projection
        return activated

    def promote_existing_plan_task(
        self,
        project_id: str,
        *,
        promoted_by: str,
        existing_task_promotion: dict[str, Any],
    ) -> dict[str, Any]:
        """Promote one already-recorded queued Delta without duplicating it."""

        contract = dict(existing_task_promotion)
        promotion_id = str(contract.get("promotion_id") or "").strip()
        session_id = str(contract.get("session_id") or "").strip()
        old_active_task_id = str(contract.get("old_active_task_id") or "").strip()
        promoted_task_id = str(contract.get("promoted_task_id") or "").strip()
        expected_host_task_id = str(contract.get("expected_host_task_id") or "").strip()
        reason = str(contract.get("reason") or "").strip()
        for field, value in (
            ("promotion_id", promotion_id),
            ("session_id", session_id),
            ("old_active_task_id", old_active_task_id),
            ("promoted_task_id", promoted_task_id),
            ("expected_host_task_id", expected_host_task_id),
            ("reason", reason),
        ):
            require(
                bool(value),
                "PLAN_EXISTING_TASK_PROMOTION_CONTRACT_INVALID",
                "Existing-task promotion is missing one exact identity or reason.",
                status="BLOCKED",
                field=field,
            )
        require(
            contract.get("expected_candidate_absent") is True
            and contract.get("expected_pending_hil") is False
            and contract.get("expected_pointer_move") is False
            and contract.get("preserve_task_identity") is True,
            "PLAN_EXISTING_TASK_PROMOTION_NON_EFFECTS_REQUIRED",
            "Promotion must explicitly forbid candidate, HIL, pointer movement, and task duplication.",
            status="BLOCKED",
        )
        session = self.load(project_id, session_id)
        pointer = self.store.pointer(project_id)
        backlog = self.store.backlog_status(project_id)
        active_ids = [str(row["task_id"]) for row in backlog["active"]]
        require(
            session.state == SessionState.TASK_CLASSIFIED
            and session.candidate_id is None
            and not bool(session.metadata.get("pending_hil"))
            and not isinstance(session.metadata.get("pending_task"), dict)
            and pointer.accepted_pv == session.accepted_pv
            and pointer.generation == session.accepted_pointer_generation,
            "PLAN_EXISTING_TASK_PROMOTION_SESSION_BOUNDARY_INVALID",
            "Promotion requires the exact candidate-free classified session and pointer.",
            status="BLOCKED",
        )
        require(
            active_ids == [old_active_task_id]
            and session.metadata.get("active_backlog_task_id") == old_active_task_id
            and session.metadata.get("current_host_session_id")
            == expected_host_task_id,
            "PLAN_EXISTING_TASK_PROMOTION_BINDING_MISMATCH",
            "The live Plan, governed session, and Task8 host identity do not agree.",
            status="MISMATCH",
            active_task_ids=active_ids,
            session_backlog_task_id=session.metadata.get("active_backlog_task_id"),
            current_host_session_id=session.metadata.get("current_host_session_id"),
        )
        activated = self.store.promote_existing_priority_task(
            project_id,
            promotion_id=promotion_id,
            old_active_task_id=old_active_task_id,
            promoted_task_id=promoted_task_id,
            session_id=session_id,
            runtime_task_id=promoted_task_id,
            promoted_by=promoted_by,
            reason_sha256=sha256_bytes(reason.encode("utf-8")),
            expected_backlog_sha256=str(contract.get("expected_backlog_sha256") or ""),
            expected_canonical_plan_sha256=str(
                contract.get("expected_canonical_plan_sha256") or ""
            ),
            expected_executable_projection_sha256=str(
                contract.get("expected_executable_projection_sha256") or ""
            ),
            expected_physical_final_task_id=str(
                contract.get("expected_physical_final_task_id") or ""
            ),
        )
        promoted = next(
            row for row in activated["tasks"] if row["task_id"] == promoted_task_id
        )
        replacement_contract = classify_task(
            task_id=promoted_task_id,
            task_class=str(promoted["task_class"]),
            requested_outcome=str(promoted["requested_outcome"]),
            permitted_paths=cast(list[str], promoted["permitted_paths"]),
            permitted_tools=cast(list[str], promoted["permitted_tools"]),
            acceptance_checks=cast(list[str], promoted["acceptance_checks"]),
            stop_condition=str(promoted["stop_condition"]),
        ).as_dict()
        rebound_at = utc_now()
        rebind_body = {
            "schema": "evidence-lane.plan-existing-task-session-rebind.v1",
            "status": "PASS",
            "promotion_id": promotion_id,
            "project_id": project_id,
            "session_id": session_id,
            "host_task_id": expected_host_task_id,
            "paused_task_id": old_active_task_id,
            "active_task_id": promoted_task_id,
            "runtime_task_id": promoted_task_id,
            "stable_task_identity_preserved": True,
            "candidate_created": False,
            "pending_hil": False,
            "pointer_moved": False,
            "goal_completed": False,
            "rebound_at": rebound_at,
        }
        rebind_receipt = {
            **rebind_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(rebind_body)),
        }
        session.task = replacement_contract
        session.metadata["active_backlog_task_id"] = promoted_task_id
        session.metadata["active_backlog_task_status"] = "ACTIVE"
        session.metadata["run_id"] = (
            f"run_existing_priority_{sha256_bytes(promotion_id.encode('utf-8'))[:24].lower()}"
        )
        session.metadata["source_update_confirmed"] = False
        session.metadata.setdefault("existing_task_promotion_rebinds", []).append(
            rebind_receipt
        )
        self._save(session)
        ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="plan.existing_task_promotion.rebound",
            visible_payload=rebind_receipt,
            occurred_at=rebound_at,
            session_id=session_id,
            task_id=promoted_task_id,
            run_id=str(session.metadata.get("run_id") or "") or None,
            event_id=f"{promotion_id}__session_rebound",
        )
        host_projection = self._prepare_host_plan_rehydration(
            project_id,
            session,
            trigger="ACTIVE_ROW_TRANSITION",
            trigger_event_id=f"{promotion_id}__host_projection",
            host_goal_active=bool(contract.get("host_goal_active")),
            affected_plan_task_ids=[promoted_task_id, old_active_task_id],
            fixed_window_task_ids=[
                str(row["task_id"])
                for row in cast(
                    list[dict[str, Any]],
                    cast(dict[str, Any], activated["goal_projection"])["rows"],
                )[:9]
            ],
            reuse_previous_window=False,
        )
        activated["existing_task_session_rebind"] = rebind_receipt
        activated["host_plan_rehydration"] = host_projection
        return activated

    def ensure_installation(self) -> dict[str, Any]:
        path = self.store.root / "installation.json"
        if path.exists():
            payload = self.installation_status()
            additions = {
                "display_name": "Evidence Lane",
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
            "display_name": "Evidence Lane",
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
        persistence_route: dict[str, Any],
        ephemeral: bool,
        flash: dict[str, Any],
        runtime_context: dict[str, Any] | None = None,
        host_session_id: str | None = None,
        client_can_edit_source: bool | None = None,
        server_has_durable_filesystem: bool | None = None,
        host_entry_consumption: dict[str, Any] | None = None,
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
        host_kind = normalize_host_kind(host)
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
        entry_validation: dict[str, Any] | None = None
        entry_freshness: dict[str, Any] = {
            "state": "NO_ACCEPTED_PV",
            "reason": "PV1 has not been accepted for this project.",
        }
        if pointer.accepted_pv:
            if self.store.uses_external_project_authority(project_id):
                continuity = self.store.live_root_pointer_continuity(
                    project_id,
                    pointer.accepted_pv,
                )
                validation = {
                    **continuity,
                    "promotable": True,
                    "lanes": {"status": "LIVE_ROOT_AUTHORITY", "valid": True},
                    "validation_scope": continuity["validation_scope"],
                    "accepted_artifact_available": None,
                    "accepted_artifact_integrity_validated": False,
                    "accepted_archive_queried": False,
                }
            else:
                validation = self.store.validate_accepted(
                    project_id,
                    pointer.accepted_pv,
                    require_promotable=False,
                )
                project_identity = self.store.accepted_metadata(
                    project_id, pointer.accepted_pv
                )["project_identity"]
                prior_repository = project_identity["repository"]
                mismatches = {
                    field: {
                        "accepted": prior_repository.get(field),
                        "current": repository_payload.get(field),
                    }
                    for field in ("repository_url", "owner", "name")
                    if prior_repository.get(field) != repository_payload.get(field)
                }
                require(
                    not mismatches,
                    "ACCEPTED_PV_REPOSITORY_MISMATCH",
                    "The current repository identity is not the repository bound to "
                    "the accepted PV.",
                    status="MISMATCH",
                    mismatches=mismatches,
                )
            entry_validation = validation
            require(
                validation["manifest_sha256"] == pointer.accepted_manifest_sha256,
                "ACCEPTED_POINTER_HASH_MISMATCH",
                "The active pointer does not match the accepted PV manifest.",
                status="MISMATCH",
            )
            entry_freshness = self._current_live_root_freshness(project_id)
        session_id = prefixed_id("session")
        now = utc_now()
        safe_context = redact(runtime_context or {})
        context_hash = sha256_bytes(canonical_json_bytes(safe_context))
        execution_profile = execution_profile_from_context(runtime_context)
        exact_host_session_id = str(host_session_id or "").strip() or None
        source_edit_authority = self._source_edit_authority(
            host_kind,
            client_can_edit_source,
        )
        runtime_continuity = build_runtime_continuity(
            project_id=project_id,
            governed_session_id=session_id,
            workspace_id=workspace_id,
            host=host_kind,
            host_session_id=exact_host_session_id,
            ephemeral=ephemeral,
            persistence_route=persistence_route,
            flash=flash,
            accepted_pv=pointer.accepted_pv,
            pointer_generation=pointer.generation,
            accepted_manifest_sha256=(
                entry_validation["manifest_sha256"] if entry_validation else None
            ),
            accepted_package_sha256=(
                entry_validation["package_sha256"] if entry_validation else None
            ),
            accepted_promotable_under_current_rules=(
                entry_validation["promotable"] if entry_validation else None
            ),
            host_entry_consumption=host_entry_consumption,
        )
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
                "execution_profile": execution_profile,
                "execution_profile_fields_are_nonsecret_selectors": True,
                "host_execution_profile_mutation_supported": False,
                "installation_state": installation["state"],
                "flash_authority_version": flash["authority_version"],
                "flash_authority_digest": flash["authority_digest"],
                "flash_action": flash["flash_action"],
                "flash_context_stored_in_pv": False,
                "persistence_mode": persistence_mode,
                "persistence_route": dict(persistence_route),
                "runtime_continuity": runtime_continuity,
                "host_entry_consumption": host_entry_consumption,
                "runtime_continuity_history": [
                    {
                        "host": host_kind.value,
                        "host_session_id": exact_host_session_id,
                        "continuity_receipt_sha256": runtime_continuity[
                            "continuity_receipt_sha256"
                        ],
                        "bound_at": now,
                    }
                ],
                "ephemeral_host": ephemeral,
                "server_has_durable_filesystem": server_has_durable_filesystem,
                "client_source_edit_authority": source_edit_authority,
                "current_host_session_id": exact_host_session_id,
                "host_session_history": (
                    [
                        {
                            "host_session_id": exact_host_session_id,
                            "host": host_kind.value,
                            "bound_at": now,
                        }
                    ]
                    if exact_host_session_id
                    else []
                ),
                "source_state": (
                    "ACCEPTED_ENTRY_EXACT"
                    if entry_freshness["state"] == "FRESH"
                    else "ACCEPTED_ENTRY_STALE_OR_DIFFERENT_LIVE_SOURCE"
                    if pointer.accepted_pv is not None
                    else "INITIAL_SOURCE_EXACT"
                ),
                "accepted_pv_query_scope": (
                    "CURRENT_ENTRY_AND_LIVE_SOURCE_EXACT"
                    if entry_freshness["state"] == "FRESH"
                    else "IMMUTABLE_ENTRY_STATE_ONLY_LIVE_SOURCE_DIFFERS"
                    if pointer.accepted_pv is not None
                    else "NO_ACCEPTED_PV"
                ),
                "entry_pv": pointer.accepted_pv,
                "entry_manifest_sha256": (
                    entry_validation["manifest_sha256"] if entry_validation else None
                ),
                "entry_package_sha256": (
                    entry_validation["package_sha256"] if entry_validation else None
                ),
                "entry_freshness": entry_freshness,
                "highest_accepted_ordinal": self.store.highest_accepted_ordinal(
                    project_id
                ),
                "persistent_store": str(self.store.root),
                "entry_history": [
                    {
                        "turn": 1,
                        "entry_pv": pointer.accepted_pv,
                        "pointer_generation": pointer.generation,
                        "manifest_sha256": (
                            entry_validation["manifest_sha256"]
                            if entry_validation
                            else None
                        ),
                        "package_sha256": (
                            entry_validation["package_sha256"]
                            if entry_validation
                            else None
                        ),
                        "host_session_id": exact_host_session_id,
                        "entered_at": now,
                    }
                ],
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
        try:
            runtime_activation = self.runtime_activation.activate(
                project_id=project_id,
                session_id=session_id,
                host_session_id=exact_host_session_id,
                flash=flash,
            )
        except Exception:
            # A boot without live attachment is not an active governed runtime.
            # Preserve the failed attempt for audit, release the one-session gate,
            # and fail closed so a later Boot can retry atomically.
            session.metadata["closed_at"] = utc_now()
            session.metadata["close_reason"] = "RUNTIME_ACTIVATION_FAILED"
            self._save(session)
            if active_path.is_file():
                active_payload = json.loads(active_path.read_text(encoding="utf-8"))
                if active_payload.get("session_id") == session_id:
                    active_path.unlink()
            lineage.append(
                event_type="session.boot.runtime_activation_failed",
                visible_payload={
                    "session_id": session_id,
                    "active_session_released": True,
                    "pointer_moved": False,
                    "hil_approval_inferred": False,
                },
                occurred_at=utc_now(),
                session_id=session_id,
            )
            raise
        session.state = transition(
            session.state,
            LifecycleEvent.FLASH_VERIFIED,
            SessionState.BOOTED,
        )
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
                "entry_pv": pointer.accepted_pv,
                "entry_manifest_sha256": session.metadata["entry_manifest_sha256"],
                "entry_package_sha256": session.metadata["entry_package_sha256"],
                "entry_freshness": entry_freshness,
                "highest_accepted_ordinal": session.metadata[
                    "highest_accepted_ordinal"
                ],
                "persistent_store": str(self.store.root),
                "runtime_context_sha256": context_hash,
                "runtime_context_stored_in_pv": False,
                "flash_authority_digest": flash["authority_digest"],
                "flash_context_stored_in_pv": False,
                "host_session_id": exact_host_session_id,
                "client_source_edit_authority": source_edit_authority,
                "runtime_continuity_receipt_sha256": runtime_continuity[
                    "continuity_receipt_sha256"
                ],
            },
            occurred_at=now,
            session_id=session_id,
        )
        entry_action = (
            "BUILD_PV1_CANDIDATE"
            if pointer.accepted_pv is None
            else "CLASSIFY_ONE_TASK"
            if entry_freshness["state"] == "FRESH"
            else "REVIEW_STALE_ACCEPTED_ENTRY_BEFORE_MUTATING_TASK"
        )
        next_action_contract = boot_next_action(entry_action=entry_action)
        return {
            "status": "PASS",
            "session": session.as_dict(),
            "installation": installation,
            "session_flash": flash,
            "runtime_activation": runtime_activation,
            "runtime_continuity": runtime_continuity,
            "entry_action": entry_action,
            "ordered_source_intake_commands": list(SOURCE_INTAKE_COMMANDS),
            "suggested_next_prompt": next_action_contract["suggested_next_prompt"],
            "next_action_contract": next_action_contract,
            "persistent_state_envelope": {
                "store": str(self.store.root),
                "accepted_pv": pointer.accepted_pv,
                "highest_accepted_ordinal": self.store.highest_accepted_ordinal(
                    project_id
                ),
                "entry_pv": session.metadata["entry_pv"],
                "pointer_generation": pointer.generation,
                "entry_manifest_sha256": session.metadata["entry_manifest_sha256"],
                "entry_package_sha256": session.metadata["entry_package_sha256"],
                "freshness": entry_freshness,
                "pending_candidate": None,
                "pending_hil": False,
            },
        }

    def resume(
        self,
        *,
        project_id: str,
        host: HostKind | str,
        host_session_id: str,
        persistence_mode: str,
        persistence_route: dict[str, Any],
        ephemeral: bool,
        client_can_edit_source: bool | None = None,
        server_has_durable_filesystem: bool | None = None,
        runtime_context: dict[str, Any] | None = None,
        flash: dict[str, Any],
        host_entry_consumption: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Bind a fresh host prompt session to the one persistent governed session."""
        active_path = self._active_path(project_id)
        require(
            active_path.is_file(),
            "NO_ACTIVE_SESSION_TO_RESUME",
            "No governed Evidence Lane session is active for this project.",
            status="BLOCKED",
            project_id=project_id,
        )
        active = json.loads(active_path.read_text(encoding="utf-8"))
        session = self.load(project_id, str(active["session_id"]))
        require(
            not session.metadata.get("closed_at"),
            "ACTIVE_SESSION_ALREADY_CLOSED",
            "The recorded active session is closed and cannot be resumed.",
            status="BLOCKED",
            session_id=session.session_id,
        )
        exact_host_session_id = host_session_id.strip()
        require(
            bool(exact_host_session_id) and len(exact_host_session_id) <= 256,
            "HOST_SESSION_ID_INVALID",
            "Resume requires the exact host session ID supplied by SessionStart.",
            status="BLOCKED",
        )
        host_kind = normalize_host_kind(host)
        pointer = self.store.pointer(project_id)
        require(
            pointer.generation == session.accepted_pointer_generation
            and pointer.accepted_pv == session.accepted_pv,
            "RESUME_POINTER_STALE",
            "The accepted pointer changed outside the persistent governed session.",
            status="STALE",
            pointer=pointer.as_dict(),
            session_pointer_generation=session.accepted_pointer_generation,
            session_accepted_pv=session.accepted_pv,
        )
        previous_continuity = session.metadata.get("runtime_continuity")
        if isinstance(previous_continuity, dict):
            validate_runtime_continuity(previous_continuity)
        entry_validation: dict[str, Any] | None = None
        if pointer.accepted_pv:
            entry_validation = self.accepted_entry_validation(
                project_id,
                pointer.accepted_pv,
                session=session,
            )
            require(
                entry_validation["manifest_sha256"] == pointer.accepted_manifest_sha256,
                "RESUME_ACCEPTED_POINTER_HASH_MISMATCH",
                "The accepted package no longer matches the pointer at resume.",
                status="MISMATCH",
            )
        now = utc_now()
        session.host = host_kind
        session.metadata["current_host_session_id"] = exact_host_session_id
        history = session.metadata.setdefault("host_session_history", [])
        if not any(
            row.get("host_session_id") == exact_host_session_id for row in history
        ):
            history.append(
                {
                    "host_session_id": exact_host_session_id,
                    "host": host_kind.value,
                    "bound_at": now,
                }
            )
        session.metadata["persistence_mode"] = persistence_mode
        runtime_continuity = build_runtime_continuity(
            project_id=project_id,
            governed_session_id=session.session_id,
            workspace_id=session.workspace_id,
            host=host_kind,
            host_session_id=exact_host_session_id,
            ephemeral=ephemeral,
            persistence_route=persistence_route,
            flash=flash,
            accepted_pv=pointer.accepted_pv,
            pointer_generation=pointer.generation,
            accepted_manifest_sha256=(
                entry_validation["manifest_sha256"] if entry_validation else None
            ),
            accepted_package_sha256=(
                entry_validation["package_sha256"] if entry_validation else None
            ),
            accepted_promotable_under_current_rules=(
                entry_validation["promotable"] if entry_validation else None
            ),
            accepted_validation_scope=(
                str(entry_validation.get("validation_scope") or "")
                if entry_validation
                else None
            ),
            accepted_artifact_available=(
                entry_validation.get("accepted_artifact_available")
                if entry_validation
                else None
            ),
            accepted_archive_queried=(
                bool(entry_validation.get("accepted_archive_queried", True))
                if entry_validation
                else None
            ),
            host_entry_consumption=host_entry_consumption,
        )
        if isinstance(previous_continuity, dict):
            previous_receipt_sha256 = str(
                previous_continuity.get("continuity_receipt_sha256") or ""
            )
            archived_receipts = session.metadata.setdefault(
                "runtime_continuity_receipt_archive", []
            )
            if not any(
                row.get("continuity_receipt_sha256") == previous_receipt_sha256
                for row in archived_receipts
            ):
                archived_receipts.append(
                    {
                        "continuity_receipt_sha256": previous_receipt_sha256,
                        "receipt": previous_continuity,
                        "disposition": "SUPERSEDED_BY_FRESH_RESUME_RECEIPT",
                        "archived_at": now,
                    }
                )
        session.metadata["persistence_route"] = dict(persistence_route)
        session.metadata["runtime_continuity"] = runtime_continuity
        session.metadata["host_entry_consumption"] = host_entry_consumption
        session.metadata.setdefault("runtime_continuity_history", []).append(
            {
                "host": host_kind.value,
                "host_session_id": exact_host_session_id,
                "continuity_receipt_sha256": runtime_continuity[
                    "continuity_receipt_sha256"
                ],
                "bound_at": now,
            }
        )
        session.metadata["ephemeral_host"] = ephemeral
        session.metadata["server_has_durable_filesystem"] = (
            server_has_durable_filesystem
        )
        session.metadata["client_source_edit_authority"] = self._source_edit_authority(
            host_kind, client_can_edit_source
        )
        safe_context = redact(runtime_context or {})
        session.metadata["resume_runtime_context_sha256"] = sha256_bytes(
            canonical_json_bytes(safe_context)
        )
        destination_profile = execution_profile_from_context(runtime_context)
        if destination_profile:
            session.metadata["execution_profile"] = destination_profile
        session.metadata["host_execution_profile_mutation_supported"] = False
        self._save(session)
        event = ChatLineage(self._lineage_path(project_id, session.session_id)).append(
            event_type="session.resumed",
            visible_payload={
                "host": host_kind.value,
                "host_session_id": exact_host_session_id,
                "state": session.state.value,
                "accepted_pv": session.accepted_pv,
                "entry_pv": session.metadata.get("entry_pv"),
                "pointer_generation": pointer.generation,
                "persistence_mode": persistence_mode,
                "client_source_edit_authority": session.metadata[
                    "client_source_edit_authority"
                ],
                "runtime_continuity_receipt_sha256": runtime_continuity[
                    "continuity_receipt_sha256"
                ],
                "pointer_moved": False,
            },
            occurred_at=now,
            session_id=session.session_id,
        )
        runtime_activation = self.runtime_activation.activate(
            project_id=project_id,
            session_id=session.session_id,
            host_session_id=exact_host_session_id,
            flash=flash,
        )
        if session.state in {SessionState.PV1_CANDIDATE, SessionState.PVN1_CANDIDATE}:
            entry_action = "PRESENT_PENDING_HIL"
        elif session.state in {
            SessionState.CORRECTION_TASK_PENDING,
            SessionState.RESEARCH_TASK_PENDING,
        }:
            entry_action = "CLASSIFY_EXACT_STORED_FOLLOW_UP"
        elif session.state == SessionState.BOOTED and session.accepted_pv is None:
            entry_action = "BUILD_PV1_CANDIDATE"
        elif session.task is not None:
            entry_action = "CONTINUE_ONE_ACTIVE_TASK"
        else:
            entry_action = "CLASSIFY_ONE_TASK"
        next_action_contract = boot_next_action(entry_action=entry_action)
        return {
            "status": "PASS",
            "resumed": True,
            "session": session.as_dict(),
            "pointer": pointer.as_dict(),
            "entry_action": entry_action,
            "ordered_source_intake_commands": list(SOURCE_INTAKE_COMMANDS),
            "suggested_next_prompt": next_action_contract["suggested_next_prompt"],
            "next_action_contract": next_action_contract,
            "event": event,
            "runtime_activation": runtime_activation,
            "runtime_continuity": runtime_continuity,
        }

    def accepted_entry_validation(
        self,
        project_id: str,
        pv_id: str,
        *,
        session: SessionRecord | None = None,
    ) -> dict[str, Any]:
        """Validate legacy bytes or external live-root continuity.

        For external project authority the immutable accepted ZIP is never an
        ordinary query/resume dependency.  The accepted pointer supplies
        baseline identity and the exact sealed runtime-continuity receipt
        supplies the previously validated hashes. Opening an accepted archive
        remains an explicit user inspection operation, never a public reentry
        side effect.
        """

        external_project_authority = self.store.uses_external_project_authority(
            project_id
        )
        if not external_project_authority:
            artifact = self.store.accepted_path(project_id, pv_id)
            validation = (
                validate_pv_package(artifact, require_promotable=False)
                if artifact.is_dir()
                else self.store.validate_accepted(
                    project_id,
                    pv_id,
                    require_promotable=False,
                )
            )
            return {
                **validation,
                "validation_scope": "CURRENT_ACCEPTED_ARTIFACT",
                "accepted_artifact_available": True,
                "accepted_artifact_integrity_validated": True,
                "continuity_reference_integrity_validated": False,
            }

        exact_session = session
        if exact_session is None:
            active_path = self._active_path(project_id)
            require(
                active_path.is_file(),
                "ACCEPTED_CONTINUITY_SESSION_REQUIRED",
                "External live-root reentry requires the exact active session continuity receipt.",
                status="MISMATCH",
                project_id=project_id,
                pv_id=pv_id,
            )
            active = json.loads(active_path.read_text(encoding="utf-8"))
            exact_session = self.load(project_id, str(active.get("session_id") or ""))

        continuity_value = exact_session.metadata.get("runtime_continuity")
        require(
            isinstance(continuity_value, dict),
            "ACCEPTED_CONTINUITY_REFERENCE_REQUIRED",
            "External live-root reentry requires one prior sealed runtime-continuity receipt.",
            status="MISMATCH",
            project_id=project_id,
            pv_id=pv_id,
        )
        continuity = validate_runtime_continuity(cast(dict[str, Any], continuity_value))
        entry_pointer = cast(dict[str, Any], continuity.get("entry_pointer") or {})
        pointer = self.store.pointer(project_id)
        unaccepted_candidate_is_independent = bool(
            exact_session.candidate_id is not None
            and exact_session.state
            in {SessionState.PV1_CANDIDATE, SessionState.PVN1_CANDIDATE}
        )
        session_pointer_matches = bool(
            pointer.accepted_pv == pv_id
            and pointer.generation == exact_session.accepted_pointer_generation
            and exact_session.accepted_pv == pv_id
        )
        require(
            session_pointer_matches,
            "ACCEPTED_CONTINUITY_POINTER_MISMATCH",
            "The exact session does not bind the current accepted pointer.",
            status="MISMATCH",
            project_id=project_id,
            pv_id=pv_id,
        )
        entry_pointer_matches = bool(
            entry_pointer.get("accepted_pv") == pv_id
            and int(entry_pointer.get("pointer_generation") or -1) == pointer.generation
            and entry_pointer.get("accepted_manifest_sha256")
            == pointer.accepted_manifest_sha256
            and entry_pointer.get("accepted_authority_integrity_validated") is True
        )
        if not entry_pointer_matches:
            promoted = self.store.live_root_pointer_continuity(project_id, pv_id)
            require(
                exact_session.candidate_id is None
                or unaccepted_candidate_is_independent
                or (
                    exact_session.candidate_id == promoted["candidate_id"]
                    and exact_session.state
                    in {SessionState.PVN_ACCEPTED, SessionState.PVN1_ACCEPTED}
                ),
                "ACCEPTED_CONTINUITY_CANDIDATE_CONFLICT",
                "External live-root reentry found a candidate unrelated to the promoted pointer.",
                status="MISMATCH",
                project_id=project_id,
                pv_id=pv_id,
            )
            return {
                "status": "PASS",
                "manifest_sha256": promoted["manifest_sha256"],
                "package_sha256": promoted["package_sha256"],
                "promotable": True,
                "lanes": {"status": "LIVE_ROOT_AUTHORITY", "valid": False},
                "storage_kind": "EXTERNAL_WORKING_ROOT_POINTER_REFERENCE",
                "validation_scope": promoted["validation_scope"],
                "accepted_artifact_available": None,
                "accepted_artifact_integrity_validated": False,
                "accepted_archive_queried": False,
                "continuity_reference_integrity_validated": True,
                "runtime_continuity_receipt_sha256": continuity[
                    "continuity_receipt_sha256"
                ],
                "promotion_receipt_sha256": promoted["promotion_receipt_sha256"],
                "swap_journal_sha256": promoted.get("swap_journal_sha256"),
                "accepted_storage": {
                    "status": "NOT_QUERIED",
                    "state": "POINTER_AND_ROOT_RECEIPT_REFERENCE_ONLY",
                    "accepted_archive_queried": False,
                },
            }
        package_sha256 = (
            str(entry_pointer.get("accepted_package_sha256") or "").strip().upper()
        )
        require(
            len(package_sha256) == 64
            and all(character in "0123456789ABCDEF" for character in package_sha256),
            "ACCEPTED_CONTINUITY_PACKAGE_HASH_INVALID",
            "The prior continuity receipt has no exact accepted package hash.",
            status="MISMATCH",
            project_id=project_id,
            pv_id=pv_id,
        )
        if (
            exact_session.candidate_id is not None
            and not unaccepted_candidate_is_independent
        ):
            promoted = self.store.live_root_pointer_continuity(project_id, pv_id)
            require(
                exact_session.candidate_id == promoted["candidate_id"]
                and exact_session.state
                in {SessionState.PVN_ACCEPTED, SessionState.PVN1_ACCEPTED},
                "ACCEPTED_CONTINUITY_CANDIDATE_CONFLICT",
                "External live-root reentry found a candidate unrelated to the "
                "promoted pointer.",
                status="MISMATCH",
                project_id=project_id,
                pv_id=pv_id,
            )
        promotable = entry_pointer.get("promotable_under_current_rules") is True
        return {
            "status": "PASS",
            "manifest_sha256": pointer.accepted_manifest_sha256,
            "package_sha256": package_sha256,
            "promotable": promotable,
            "lanes": {
                "status": "LIVE_ROOT_AUTHORITY",
                "valid": False,
            },
            "storage_kind": "EXTERNAL_WORKING_ROOT_POINTER_REFERENCE",
            "validation_scope": "LIVE_ROOT_RUNTIME_CONTINUITY_REFERENCE",
            "accepted_artifact_available": None,
            "accepted_artifact_integrity_validated": False,
            "accepted_archive_queried": False,
            "continuity_reference_integrity_validated": True,
            "runtime_continuity_receipt_sha256": continuity[
                "continuity_receipt_sha256"
            ],
            "accepted_storage": {
                "status": "NOT_QUERIED",
                "state": "POINTER_REFERENCE_ONLY",
                "accepted_archive_queried": False,
            },
            "warning": {
                "code": "ACCEPTED_ARCHIVE_NOT_QUERIED_LIVE_ROOT_CONTINUITY_USED",
                "message": (
                    "Immutable accepted bytes were not opened; the exact prior "
                    "validated continuity reference and live root were used."
                ),
            },
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
        self._consume_lane_route_grant(session, result["candidate_id"])
        session.candidate_id = result["candidate_id"]
        session.state = transition(
            session.state,
            LifecycleEvent.BUILD_INITIAL,
            SessionState.PV1_CANDIDATE,
        )
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

    def bootstrap_pv0_entry(
        self,
        project_id: str,
        session_id: str,
        *,
        source_intake: dict[str, Any],
    ) -> dict[str, Any]:
        """Establish the no-HIL live-root PV0 starting authority."""

        session = self.load(project_id, session_id)
        pointer_before = self.store.pointer(project_id)
        refresh = dict(source_intake.get("working_authority_refresh") or {})
        lineage = dict(source_intake.get("chat_lineage") or {})
        require(
            self.store.uses_external_project_authority(project_id)
            and session.state == SessionState.BOOTED
            and session.accepted_pv is None
            and session.candidate_id is None
            and pointer_before.accepted_pv is None
            and pointer_before.generation == 0,
            "PV0_BOOTSTRAP_STATE_INVALID",
            "PV0 bootstrap is valid only for the first boot of a live-root project.",
            status="BLOCKED",
            state=session.state.value,
            pointer=pointer_before.as_dict(),
        )
        require(
            refresh.get("status") == "PASS"
            and refresh.get("pv0_bootstrap_pending") is True
            and refresh.get("pointer_moved") is False
            and refresh.get("candidate_created") is False
            and refresh.get("hil_inferred") is False
            and bool(str(refresh.get("receipt_sha256") or ""))
            and lineage.get("append_status") == "APPENDED"
            and bool(str(lineage.get("event_id") or "")),
            "PV0_BOOTSTRAP_SOURCE_INTAKE_INVALID",
            "Initial Build requires one exact Source Intake all-sector refresh before binding PV0.",
            status="MISMATCH",
        )
        baseline = self.store.bootstrap_pv0_baseline(
            project_id,
            source_intake_event_id=str(lineage["event_id"]),
            working_refresh_receipt_sha256=str(refresh["receipt_sha256"]),
            bootstrapped_by=session.user_id,
        )
        pointer_after = self.store.pointer(project_id)
        require(
            pointer_after.accepted_pv == "PV0"
            and pointer_after.generation == 0
            and pointer_after.accepted_manifest_sha256
            == baseline["receipt"]["receipt_sha256"],
            "PV0_BOOTSTRAP_POINTER_MISMATCH",
            "The PV0 live-root baseline did not bind the expected pointer identity.",
            status="MISMATCH",
        )
        session.accepted_pv = "PV0"
        session.accepted_pointer_generation = 0
        session.state = transition(
            session.state,
            LifecycleEvent.BOOTSTRAP_PV0,
            SessionState.PVN_ACCEPTED,
        )
        session.metadata["initial_pv0_baseline"] = {
            "receipt_sha256": baseline["receipt"]["receipt_sha256"],
            "source_intake_event_id": lineage["event_id"],
            "working_refresh_receipt_sha256": refresh["receipt_sha256"],
            "human_hil_required": False,
            "candidate_created": False,
            "accepted_archive_queried": False,
        }
        session.metadata["source_state"] = "PV0_LIVE_ROOT_BASELINE"
        session.metadata["accepted_pv_query_scope"] = "LIVE_ROOT_BASELINE_ONLY"
        self._save(session)
        event = ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="pv.pv0_baseline.established",
            visible_payload={
                "baseline_pv": "PV0",
                "pointer_generation": 0,
                "receipt_sha256": baseline["receipt"]["receipt_sha256"],
                "human_hil_required": False,
                "candidate_created": False,
                "accepted_archive_queried": False,
            },
            occurred_at=utc_now(),
            session_id=session_id,
        )
        return {
            "status": "PASS",
            "schema": "evidence-lane.pv0-build-bootstrap.v1",
            "session": session.as_dict(),
            "pointer": pointer_after.as_dict(),
            "baseline": baseline,
            "source_intake": source_intake,
            "event": event,
            "candidate": None,
            "candidate_created": False,
            "human_hil_required": False,
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
            "next_action": "COLLECT_USER_BRIEF_AND_OPEN_EVI_PLAN",
        }

    @staticmethod
    def _consume_lane_route_grant(
        session: SessionRecord,
        candidate_id: str,
    ) -> None:
        grant = session.metadata.get("source_lane_route_grant")
        if not isinstance(grant, dict) or grant.get("status") != "ARMED":
            return
        grant["status"] = "CONSUMED_RELOCKED"
        grant["candidate_id"] = candidate_id
        grant["consumed_at"] = utc_now()
        session.metadata.pop("source_lane_overrides", None)

    def configure_source_lanes(
        self,
        project_id: str,
        session_id: str,
        *,
        overrides: dict[str, str],
        granted_by: str,
        grant_id: str | None = None,
    ) -> dict[str, Any]:
        """Arm one exact route override for the next candidate build only."""
        session = self.load(project_id, session_id)
        require(
            session.state
            in {
                SessionState.BOOTED,
                SessionState.PVN_ACCEPTED,
                SessionState.PVN1_ACCEPTED,
                SessionState.PVN1_ENTRY,
            }
            and session.task is None
            and session.candidate_id is None,
            "LANE_ROUTE_GRANT_STATE_INVALID",
            "Lane route grants may be armed only at an idle entry before candidate "
            "construction.",
            status="BLOCKED",
            state=session.state.value,
        )
        require(
            1 <= len(overrides) <= 100,
            "LANE_ROUTE_GRANT_SIZE_INVALID",
            "A route grant must name between one and one hundred exact sources.",
            status="BLOCKED",
        )
        require(
            bool(granted_by.strip()),
            "LANE_ROUTE_GRANT_ACTOR_REQUIRED",
            "A visible human identity is required for a named route grant.",
            status="BLOCKED",
        )
        config = self.store.config(project_id)
        source_paths = {
            relative for relative, _ in iter_source_files(config.repository_path)
        }
        code_mode = (
            "github_code"
            if session.repository.get("provider") == "github"
            else "local_code"
        )
        normalized: dict[str, str] = {}
        for raw_path, raw_lane in sorted(overrides.items()):
            relative = raw_path.replace("\\", "/").strip("/")
            require(
                relative in source_paths,
                "LANE_ROUTE_SOURCE_NOT_FOUND",
                "Every route override must name an exact current source path.",
                status="MISMATCH",
                path=raw_path,
            )
            try:
                normalized[relative] = resolve_lane_id(raw_lane, code_mode=code_mode)
            except LaneRegistryError as exc:
                raise EvidenceLaneError(
                    "LANE_ROUTE_TARGET_INVALID",
                    str(exc),
                    status="BLOCKED",
                    details={"path": relative, "lane": raw_lane},
                ) from exc
        exact_grant_id = grant_id or prefixed_id("lanegrant")
        existing = session.metadata.get("source_lane_route_grant")
        if isinstance(existing, dict) and existing.get("grant_id") == exact_grant_id:
            require(
                existing.get("overrides") == normalized
                and existing.get("granted_by") == granted_by.strip(),
                "LANE_ROUTE_GRANT_ID_CONFLICT",
                "The lane-route grant ID already binds different authority.",
                status="BLOCKED",
            )
            return {"status": "PASS", "grant": existing}
        require(
            not isinstance(existing, dict) or existing.get("status") != "ARMED",
            "LANE_ROUTE_GRANT_ALREADY_ARMED",
            "One unconsumed lane-route grant is already armed for this session.",
            status="BLOCKED",
        )
        grant = {
            "schema": "evidence-lane.lane-route-grant.v1",
            "grant_id": exact_grant_id,
            "granted_by": granted_by.strip(),
            "overrides": normalized,
            "code_mode": code_mode,
            "status": "ARMED",
            "one_candidate_only": True,
            "snapshot_relock_required": True,
            "granted_at": utc_now(),
        }
        session.metadata["source_lane_overrides"] = normalized
        session.metadata["source_lane_route_grant"] = grant
        self._save(session)
        ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="lane.route_grant.armed",
            visible_payload=grant,
            occurred_at=utc_now(),
            session_id=session_id,
        )
        return {"status": "PASS", "grant": grant, "session": session.as_dict()}

    def _verify_task_checkpoint_advance(
        self,
        project_id: str,
        session_id: str,
        *,
        completed_backlog_task_id: str,
        replacement_backlog_task_id: str,
        replacement_task: TaskContract,
        verification_kind: str,
        verification_proof: dict[str, Any] | None,
        native_route_receipt: dict[str, Any] | None,
        installed_surface_inventory: dict[str, Any] | None,
        project_panel_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Seal one reusable candidate-free checkpoint after native proof."""

        session = self.load(project_id, session_id)
        pointer = self.store.pointer(project_id)
        proof = verification_proof or {}
        proof_body = {
            key: value for key, value in proof.items() if key != "receipt_sha256"
        }
        exact_binding_proof = (
            verification_kind == "EXACT_TASK_PROJECT_SESSION_BINDING"
            and proof.get("schema")
            == "evidence-lane.codex-exact-task-project-session-binding.v1"
        )
        acceptance_proof = (
            verification_kind == "ACTIVE_TASK_ACCEPTANCE"
            and proof.get("schema")
            == "evidence-lane.active-task-acceptance-checkpoint.v1"
        )
        per_delta_proof = (
            verification_kind == "PER_DELTA_LOCAL_VERIFICATION"
            and proof.get("schema")
            == "evidence-lane.per-delta-local-verification-checkpoint.v1"
        )
        require(
            bool(verification_kind)
            and (exact_binding_proof or acceptance_proof or per_delta_proof)
            and proof.get("status") == "PASS"
            and proof.get("project_id") == project_id
            and proof.get("evidence_session_id") == session_id
            and proof.get("active_plan_row", {}).get("task_id")
            == completed_backlog_task_id
            and proof.get("active_plan_row", {}).get("status") == "in_progress"
            and proof.get("active_plan_row", {}).get("lifecycle_status") == "ACTIVE"
            and proof.get("accepted_pointer", {}).get("accepted_pv")
            == pointer.accepted_pv
            and proof.get("accepted_pointer", {}).get("generation")
            == pointer.generation
            and proof.get("accepted_pointer", {}).get("manifest_sha256")
            == pointer.accepted_manifest_sha256
            and proof.get("governed_host_session_id")
            == session.metadata.get("current_host_session_id")
            and proof.get("task_title_used") is False
            and proof.get("cwd_used") is False
            and proof.get("candidate_created") is False
            and proof.get("pending_hil") is False
            and proof.get("pointer_moved") is False
            and proof.get("hil_inferred") is False
            and proof.get("receipt_sha256")
            == sha256_bytes(canonical_json_bytes(proof_body)),
            "TASK_CHECKPOINT_ADVANCE_PROOF_MISMATCH",
            "The active task lacks one exact, self-sealed native verification proof.",
            status="MISMATCH",
            verification_kind=verification_kind,
            proof_error=proof.get("error"),
        )
        backlog = self.store.backlog_status(project_id)
        completed_task = next(
            (
                row
                for row in backlog["tasks"]
                if row.get("task_id") == completed_backlog_task_id
            ),
            None,
        )
        per_delta_required = isinstance(completed_task, dict) and (
            str(completed_task.get("task_id") or "").startswith("EL-CODEX-T6-PARITY-")
            or str(completed_task.get("commit_batch_id") or "") == "PV13_TASK6_PARITY"
            or str(completed_task.get("current_contract_authority") or "")
            == "ACTIVE_CONTRACT_REBIND"
        )
        require(
            not per_delta_required or per_delta_proof,
            "TASK_CHECKPOINT_ADVANCE_DELTA_VERIFICATION_REQUIRED",
            "A strict package-parity row cannot advance on a generic PASS checkpoint.",
            status="BLOCKED",
            completed_backlog_task_id=completed_backlog_task_id,
        )
        if acceptance_proof:
            acceptance = proof.get("acceptance_contract")
            evidence = proof.get("acceptance_evidence")
            require(
                isinstance(completed_task, dict)
                and isinstance(acceptance, dict)
                and acceptance.get("checks") == completed_task.get("acceptance_checks")
                and acceptance.get("checks_sha256")
                == sha256_bytes(
                    canonical_json_bytes(completed_task.get("acceptance_checks"))
                )
                and isinstance(evidence, dict)
                and evidence.get("event_type")
                in {"task.test.output", "task.build.output"}
                and len(str(evidence.get("event_sha256") or "")) == 64
                and len(str(evidence.get("visible_payload_sha256") or "")) == 64,
                "TASK_CHECKPOINT_ADVANCE_ACCEPTANCE_MISMATCH",
                "The checkpoint proof does not cover the active task's exact acceptance contract.",
                status="MISMATCH",
            )
        install_deferral_facts: dict[str, Any] = {"valid": False}
        if per_delta_proof:
            raw_delta = proof.get("delta_verification")
            delta = (
                cast(dict[str, Any], raw_delta) if isinstance(raw_delta, dict) else {}
            )
            delta_body = {
                key: value for key, value in delta.items() if key != "receipt_sha256"
            }
            exact_checks = (
                completed_task.get("acceptance_checks", [])
                if isinstance(completed_task, dict)
                else []
            )
            exact_dependencies = (
                completed_task.get("dependencies", [])
                if isinstance(completed_task, dict)
                else []
            )
            changed_paths = (
                delta.get("changed_paths", []) if isinstance(delta, dict) else []
            )
            test_runs = delta.get("test_runs", []) if isinstance(delta, dict) else []
            raw_adaptive_receipt = proof.get("adaptive_delta_exit_receipt")
            adaptive_receipt = (
                cast(dict[str, Any], raw_adaptive_receipt)
                if isinstance(raw_adaptive_receipt, dict)
                else None
            )
            install_deferral_facts = adaptive_install_deferral_facts(
                adaptive_receipt if isinstance(adaptive_receipt, dict) else None,
                project_id=project_id,
                session_id=session_id,
                active_task_id=completed_backlog_task_id,
                plan_tasks=backlog["tasks"],
            )
            source_catalog = {
                "tools": NATIVE_TOOL_COUNT,
                "read": NATIVE_READ_TOOL_COUNT,
                "write": NATIVE_WRITE_TOOL_COUNT,
                "skills": GOVERNED_SKILL_COUNT,
            }
            if adaptive_receipt is None:
                deferral_binding_matches = (
                    delta.get("install_disposition") is None
                    and delta.get("adaptive_delta_exit_receipt_sha256") is None
                )
            else:
                deferral_binding_matches = (
                    install_deferral_facts["valid"] is True
                    and delta.get("install_disposition")
                    == adaptive_receipt.get("install_disposition")
                    and delta.get("adaptive_delta_exit_receipt_sha256")
                    == install_deferral_facts["receipt_sha256"]
                )
            repository = Path(self.store.config(project_id).repository_path).resolve()
            live_paths_match = isinstance(changed_paths, list) and all(
                isinstance(row, dict)
                and (repository / Path(str(row.get("path") or ""))).resolve().is_file()
                and (repository / Path(str(row.get("path") or "")))
                .resolve()
                .is_relative_to(repository)
                and sha256_file(
                    (repository / Path(str(row.get("path") or ""))).resolve()
                )
                == row.get("sha256")
                for row in changed_paths
            )
            test_runs_match = isinstance(test_runs, list) and all(
                isinstance(row, dict)
                and row.get("status") == "PASS"
                and sha256_bytes(str(row.get("command") or "").encode("utf-8"))
                == row.get("command_sha256")
                and sha256_bytes(str(row.get("output") or "").encode("utf-8"))
                == row.get("output_sha256")
                and bool(row.get("negative_cases"))
                for row in test_runs
            )
            require(
                isinstance(completed_task, dict)
                and isinstance(delta, dict)
                and delta.get("schema")
                == "evidence-lane.per-delta-local-verification.v1"
                and delta.get("status") == "PASS"
                and delta.get("receipt_sha256")
                == sha256_bytes(canonical_json_bytes(delta_body))
                and delta.get("active_task_id") == completed_backlog_task_id
                and delta.get("task_contract_sha256")
                == proof.get("active_plan_row", {}).get("task_contract_sha256")
                and delta.get("dependency_generation") == pointer.generation
                and delta.get("dependency_task_ids") == exact_dependencies
                and delta.get("acceptance_checks") == exact_checks
                and delta.get("acceptance_checks_sha256")
                == sha256_bytes(canonical_json_bytes(exact_checks))
                and delta.get("source_catalog") == source_catalog
                and deferral_binding_matches
                and delta.get("post_worktree_sha256")
                == calculate_worktree_sha256(repository)
                and len(changed_paths) >= 1
                and live_paths_match
                and len(test_runs) >= 1
                and test_runs_match
                and delta.get("candidate_created") is False
                and delta.get("pending_hil") is False
                and delta.get("pointer_moved") is False
                and delta.get("hil_inferred") is False
                and delta.get("git_executed") is False
                and delta.get("install_executed") is False,
                "TASK_CHECKPOINT_ADVANCE_DELTA_VERIFICATION_MISMATCH",
                "The checkpoint does not bind exact live per-Delta code and test evidence.",
                status="MISMATCH",
            )
        require(
            session.state == SessionState.TASK_CLASSIFIED
            and session.candidate_id is None
            and not session.metadata.get("pending_hil")
            and session.metadata.get("active_backlog_task_id")
            == completed_backlog_task_id
            and session.metadata.get("active_backlog_task_status") == "ACTIVE"
            and session.metadata.get("client_source_edit_authority") == "DIRECT"
            and isinstance(session.task, dict),
            "TASK_CHECKPOINT_ADVANCE_SESSION_MISMATCH",
            "The governed session is not at the exact candidate-free active row.",
            status="MISMATCH",
        )

        route = native_route_receipt or {}
        surface = installed_surface_inventory or {}
        panel = project_panel_snapshot or {}
        surface_core = {
            key: surface.get(key)
            for key in (
                "schema",
                "plugin_version",
                "hooks",
                "skills",
                "catalog",
                "raw_paths_included",
            )
        }
        expected_catalog = {
            "tools": NATIVE_TOOL_COUNT,
            "read": NATIVE_READ_TOOL_COUNT,
            "write": NATIVE_WRITE_TOOL_COUNT,
            "skills": GOVERNED_SKILL_COUNT,
        }
        panel_facts = {
            str(row.get("label")): row.get("value")
            for row in panel.get("facts", [])
            if isinstance(row, dict)
        }
        panel_hil = panel.get("hil")
        installed_catalog = dict(surface.get("catalog") or {})
        deferred_install = install_deferral_facts.get("valid") is True
        deferred_catalog_valid = (
            set(installed_catalog) == {"tools", "read", "write", "skills"}
            and all(isinstance(value, int) for value in installed_catalog.values())
            and installed_catalog.get("tools")
            == installed_catalog.get("read", -1) + installed_catalog.get("write", -1)
            and 0 < installed_catalog.get("tools", 0) <= expected_catalog["tools"]
            and 0 <= installed_catalog.get("read", -1) <= expected_catalog["read"]
            and 0 <= installed_catalog.get("write", -1) <= expected_catalog["write"]
            and installed_catalog.get("skills") == expected_catalog["skills"]
        )
        require(
            route.get("schema") == "evidence-lane.native-mcp-route-receipt.v1"
            and route.get("status") == "PASS"
            and route.get("server_identity") == "evidence-lane"
            and route.get("canonical_tool_namespace") == "mcp__evidence_lane__"
            and route.get("exposure_profile") == "FULL_LIFECYCLE"
            and route.get("tool_count") == installed_catalog.get("tools")
            and route.get("tool_names_unique") is True
            and route.get("project_route_argument_required") is True
            and route.get("cross_project_fallback_allowed") is False
            and len(str(route.get("tool_catalog_sha256") or "")) == 64,
            "TASK_CHECKPOINT_ADVANCE_NATIVE_ROUTE_MISMATCH",
            "Checkpoint advancement requires the exact current native route.",
            status="MISMATCH",
        )
        require(
            surface.get("schema")
            == "evidence-lane.codex-installed-surface-inventory.v2"
            and str(surface.get("plugin_version") or "").split("+", 1)[0]
            == ENGINE_VERSION
            and (
                (not deferred_install and installed_catalog == expected_catalog)
                or (deferred_install and deferred_catalog_valid)
            )
            and isinstance(surface.get("skills"), dict)
            and surface["skills"].get("count") == installed_catalog.get("skills")
            and surface.get("raw_paths_included") is False
            and surface.get("surface_inventory_sha256")
            == sha256_bytes(canonical_json_bytes(surface_core))
            and proof.get("running_plugin", {}).get("plugin_version")
            == surface.get("plugin_version")
            and proof.get("running_plugin", {}).get("surface_inventory_sha256")
            == surface.get("surface_inventory_sha256"),
            "TASK_CHECKPOINT_ADVANCE_INSTALLED_SURFACE_MISMATCH",
            "The checkpoint proof and running installed v2 surface do not agree.",
            status="MISMATCH",
        )
        require(
            panel.get("schema") == PROJECT_PANEL_SCHEMA
            and panel.get("panel") == "project"
            and panel.get("status") == "PASS"
            and panel.get("read_only") is True
            and panel_facts.get("Project") == project_id
            and panel_facts.get("Accepted PV") == str(pointer.accepted_pv)
            and panel_facts.get("Pointer generation") == str(pointer.generation)
            and panel_facts.get("Active state") == SessionState.TASK_CLASSIFIED.value
            and panel_facts.get("Pending candidate") == "NONE"
            and isinstance(panel_hil, dict)
            and panel_hil.get("pending") is False
            and panel_hil.get("candidate") is None,
            "TASK_CHECKPOINT_ADVANCE_PROJECT_PANEL_MISMATCH",
            "The project panel is not at the candidate-free accepted-pointer boundary.",
            status="MISMATCH",
        )

        goal = cast(dict[str, Any], backlog["goal_projection"])
        rows = cast(list[dict[str, Any]], goal["rows"])
        numbers = [int(row["number"]) for row in rows]
        active = cast(list[dict[str, Any]], backlog["active"])
        first_queued = next(
            (
                row
                for row in sorted(
                    backlog["tasks"], key=lambda item: int(item["sequence"])
                )
                if row.get("status") == "QUEUED"
            ),
            None,
        )
        require(
            backlog.get("status") == "PASS"
            and goal.get("canonical_authority") == "PLAN_LANE"
            and goal.get("persistent_until") == "NEXT_SIX_WAY_HIL_PRESENTED"
            and numbers == list(range(numbers[0], numbers[0] + len(numbers)))
            and len(active) == 1
            and active[0].get("task_id") == completed_backlog_task_id
            and isinstance(first_queued, dict)
            and first_queued.get("task_id") == replacement_backlog_task_id
            and rows[-1].get("panel_role") == "PHYSICALLY_FINAL_HIL",
            "TASK_CHECKPOINT_ADVANCE_PLAN_MISMATCH",
            "Plan Lane is not at the contiguous one-active checkpoint boundary.",
            status="MISMATCH",
        )
        prior_task = cast(dict[str, Any], session.task)
        prior_runtime_task_id = str(prior_task.get("task_id") or "").strip()
        require(
            bool(prior_runtime_task_id),
            "TASK_CHECKPOINT_ADVANCE_PRIOR_RUNTIME_TASK_REQUIRED",
            "The active row has no runtime task identity.",
            status="MISMATCH",
        )
        body = {
            "schema": "evidence-lane.verified-task-checkpoint-advance.v1",
            "project_id": project_id,
            "session_id": session_id,
            "verification_kind": verification_kind,
            "completed_backlog_task_id": completed_backlog_task_id,
            "replacement_backlog_task_id": replacement_backlog_task_id,
            "prior_runtime_task_id": prior_runtime_task_id,
            "prior_run_id": session.metadata.get("run_id"),
            "replacement_runtime_task_id": replacement_task.task_id,
            "successor_classified_at": utc_now(),
            "accepted_pv": pointer.accepted_pv,
            "pointer_generation": pointer.generation,
            "manifest_sha256": pointer.accepted_manifest_sha256,
            "current_canonical_plan_sha256": backlog["canonical_plan_projection"][
                "projection_sha256"
            ],
            "current_executable_projection_sha256": goal["projection_sha256"],
            "current_event_head_sha256": backlog.get("event_head_sha256"),
            "persistent_until": goal.get("persistent_until"),
            "native_route": {
                "server_identity": route.get("server_identity"),
                "canonical_tool_namespace": route.get("canonical_tool_namespace"),
                "exposure_profile": route.get("exposure_profile"),
                "tool_count": route.get("tool_count"),
                "tool_catalog_sha256": route.get("tool_catalog_sha256"),
            },
            "installed_surface": {
                "plugin_version": surface.get("plugin_version"),
                "catalog": surface.get("catalog"),
                "surface_inventory_sha256": surface.get("surface_inventory_sha256"),
            },
            "install_deferral": (install_deferral_facts if deferred_install else None),
            "project_panel_sha256": sha256_bytes(canonical_json_bytes(panel)),
            "verification_proof": proof,
            "candidate_created": False,
            "pending_hil": False,
            "pointer_moved": False,
            "hil_inferred": False,
        }
        return {
            "receipt": {
                **body,
                "receipt_sha256": sha256_bytes(canonical_json_bytes(body)),
            },
            "prior_task": prior_task,
            "backlog": backlog,
        }

    def _append_task_checkpoint_advance_lineage(
        self,
        project_id: str,
        session_id: str,
        *,
        session: SessionRecord,
        task: TaskContract,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        receipt_sha256 = str(receipt.get("receipt_sha256") or "").strip()
        require(
            len(receipt_sha256) == 64,
            "TASK_CHECKPOINT_ADVANCE_LINEAGE_RECEIPT_INVALID",
            "The checkpoint advance cannot bind deterministic lineage events.",
            status="MISMATCH",
        )
        suffix = receipt_sha256[:32].lower()
        lineage = ChatLineage(self._lineage_path(project_id, session_id))
        completion_event = lineage.append(
            event_type="task.checkpoint.completed",
            visible_payload=receipt,
            occurred_at=str(receipt["successor_classified_at"]),
            session_id=session_id,
            task_id=str(receipt["prior_runtime_task_id"]),
            run_id=(
                str(receipt.get("prior_run_id"))
                if receipt.get("prior_run_id") is not None
                else None
            ),
            event_id=f"evt_checkpoint_completed_{suffix}",
        )
        classification_event = lineage.append(
            event_type="task.classified",
            visible_payload=task.as_dict(),
            occurred_at=str(receipt["successor_classified_at"]),
            session_id=session_id,
            task_id=task.task_id,
            run_id=str(session.metadata["run_id"]),
            event_id=f"evt_checkpoint_classified_{suffix}",
        )
        return {
            "completion_event": completion_event,
            "classification_event": classification_event,
        }

    def _replay_task_checkpoint_advance(
        self,
        project_id: str,
        session_id: str,
        *,
        task: TaskContract,
        receipt: dict[str, Any],
        verification_proof: dict[str, Any] | None,
        native_route_receipt: dict[str, Any] | None,
        installed_surface_inventory: dict[str, Any] | None,
        project_panel_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        receipt_body = {
            key: value for key, value in receipt.items() if key != "receipt_sha256"
        }
        proof = verification_proof or {}
        route = native_route_receipt or {}
        surface = installed_surface_inventory or {}
        panel = project_panel_snapshot or {}
        require(
            receipt.get("schema") == "evidence-lane.verified-task-checkpoint-advance.v1"
            and receipt.get("project_id") == project_id
            and receipt.get("session_id") == session_id
            and receipt.get("replacement_backlog_task_id")
            == session.metadata.get("active_backlog_task_id")
            and receipt.get("replacement_runtime_task_id") == task.task_id
            and receipt.get("receipt_sha256")
            == sha256_bytes(canonical_json_bytes(receipt_body))
            and receipt.get("verification_proof", {}).get("receipt_sha256")
            == proof.get("receipt_sha256")
            and receipt.get("native_route", {}).get("tool_catalog_sha256")
            == route.get("tool_catalog_sha256")
            and receipt.get("installed_surface", {}).get("surface_inventory_sha256")
            == surface.get("surface_inventory_sha256")
            and receipt.get("project_panel_sha256")
            == sha256_bytes(canonical_json_bytes(panel))
            and session.task == task.as_dict()
            and session.candidate_id is None
            and not session.metadata.get("pending_hil"),
            "TASK_CHECKPOINT_ADVANCE_REPLAY_MISMATCH",
            "The retried successor no longer matches its checkpoint receipt.",
            status="MISMATCH",
        )
        plan_transition = self.store.advance_verified_task_checkpoint(
            project_id,
            completed_backlog_task_id=str(receipt["completed_backlog_task_id"]),
            replacement_backlog_task_id=str(receipt["replacement_backlog_task_id"]),
            session_id=session_id,
            prior_runtime_task_id=str(receipt["prior_runtime_task_id"]),
            replacement_contract=task.as_dict(),
            completion_receipt=receipt,
        )
        require(
            plan_transition.get("idempotent_reuse") is True,
            "TASK_CHECKPOINT_ADVANCE_REPLAY_PLAN_MISMATCH",
            "Plan Lane is not already at the exact checkpoint successor state.",
            status="MISMATCH",
        )
        lineage = self._append_task_checkpoint_advance_lineage(
            project_id,
            session_id,
            session=session,
            task=task,
            receipt=receipt,
        )
        return {
            "status": "PASS",
            "idempotent_reuse": True,
            "receipt": receipt,
            "plan_transition": plan_transition,
            "lineage": lineage,
        }

    def _seal_task_classification_binding(
        self,
        project_id: str,
        session_id: str,
        *,
        session: SessionRecord,
        task: TaskContract,
        pointer: ActivePointer,
        target_backlog_task_id: str | None,
        prior_executable_task_id: str | None,
    ) -> dict[str, Any]:
        """Seal the exact Plan, mode, lifecycle, and authority classification."""

        backlog = self.store.backlog_status(project_id)
        goal = cast(dict[str, Any], backlog["goal_projection"])
        goal_rows = cast(list[dict[str, Any]], goal.get("rows") or [])
        target_row = next(
            (row for row in goal_rows if row.get("task_id") == target_backlog_task_id),
            None,
        )
        prior_row = next(
            (
                row
                for row in cast(list[dict[str, Any]], backlog.get("tasks") or [])
                if row.get("task_id") == prior_executable_task_id
            ),
            None,
        )

        def linked_delta_ids(row: dict[str, Any] | None) -> list[str]:
            if not isinstance(row, dict):
                return []
            return [
                str(delta["delta_id"])
                for delta in cast(list[dict[str, Any]], row.get("steer_deltas") or [])
                if str(delta.get("delta_id") or "").strip()
            ]

        target_linked_delta_ids = linked_delta_ids(target_row)
        prior_linked_delta_ids = linked_delta_ids(prior_row)
        task_mode_binding = session.metadata.get("task_mode_binding")
        if isinstance(task_mode_binding, dict):
            mode_governance = task_mode_binding.get("mode_governance")
            governance_contracts = (
                cast(dict[str, Any], mode_governance).get("contracts") or []
                if isinstance(mode_governance, dict)
                else []
            )
            ordered_mode_operators = [
                {
                    "mode_id": str(contract.get("mode_id") or ""),
                    "operator_ids": [
                        str(operator.get("operator_id") or "")
                        for operator in cast(
                            list[dict[str, Any]], contract.get("operators") or []
                        )
                        if str(operator.get("operator_id") or "").strip()
                    ],
                }
                for contract in cast(list[dict[str, Any]], governance_contracts)
            ]
            mode_operator_binding = {
                "status": "BOUND",
                "selected_mode_ids": list(
                    task_mode_binding.get("selected_mode_ids") or []
                ),
                "mode_intersection": str(
                    task_mode_binding.get("mode_intersection") or ""
                ),
                "canonical_lanes": list(task_mode_binding.get("canonical_lanes") or []),
                "ordered_mode_operators": ordered_mode_operators,
                "binding_receipt_sha256": task_mode_binding.get(
                    "binding_receipt_sha256"
                ),
            }
        else:
            mode_operator_binding = {
                "status": "UNSELECTED",
                "selected_mode_ids": [],
                "mode_intersection": None,
                "canonical_lanes": [],
                "ordered_mode_operators": [],
                "binding_receipt_sha256": None,
            }

        canonical_tasks = cast(list[dict[str, Any]], backlog.get("tasks") or [])
        superseded_task_ids = [
            str(row["task_id"])
            for row in canonical_tasks
            if row.get("status") == "SUPERSEDED"
        ]
        current_goal_task_ids = {str(row.get("task_id") or "") for row in goal_rows}
        superseded_excluded = not (set(superseded_task_ids) & current_goal_task_ids)
        require(
            superseded_excluded,
            "SUPERSEDED_DELTA_VISIBLE_IN_CURRENT_PROJECTION",
            "A superseded Delta remained in the current executable Goal projection.",
            status="MISMATCH",
        )

        target_binding = (
            {
                "status": "BOUND",
                "canonical_row": target_row.get("number"),
                "task_id": target_row.get("task_id"),
                "description": target_row.get("step"),
                "lifecycle_status": target_row.get("lifecycle_status"),
                "host_status": target_row.get("status"),
                "supersedes_task_id": target_row.get("supersedes_task_id"),
                "linked_delta_ids": target_linked_delta_ids,
                "current_change_delta_id": (
                    target_linked_delta_ids[-1]
                    if target_linked_delta_ids
                    else target_row.get("task_id")
                ),
            }
            if isinstance(target_row, dict)
            else {
                "status": "UNBOUND_STANDALONE_TASK",
                "canonical_row": None,
                "task_id": None,
                "description": None,
                "lifecycle_status": None,
                "host_status": None,
                "supersedes_task_id": None,
                "linked_delta_ids": [],
                "current_change_delta_id": None,
            }
        )
        prior_executable_delta = {
            "task_id": prior_executable_task_id,
            "lifecycle_status_after_classification": (
                prior_row.get("status") if isinstance(prior_row, dict) else None
            ),
            "linked_delta_ids": prior_linked_delta_ids,
            "current_change_delta_id": (
                prior_linked_delta_ids[-1]
                if prior_linked_delta_ids
                else prior_executable_task_id
            ),
        }
        body = {
            "schema": "evidence-lane.task-classification-binding.v1",
            "status": "PASS",
            "project_id": project_id,
            "session_id": session_id,
            "runtime_task_id": task.task_id,
            "run_id": session.metadata.get("run_id"),
            "task_class": task.task_class.value,
            "requested_outcome": task.requested_outcome,
            "mode_operator_binding": mode_operator_binding,
            "lifecycle_state": session.state.value,
            "authority_boundary": {
                "write_boundary": task.write_boundary,
                "permitted_paths": list(task.permitted_paths),
                "permitted_tools": list(task.permitted_tools),
                "accepted_pv": pointer.accepted_pv,
                "pointer_generation": pointer.generation,
                "source_state": session.metadata.get("source_state"),
                "accepted_pv_query_scope": session.metadata.get(
                    "accepted_pv_query_scope"
                ),
            },
            "prior_executable_delta": prior_executable_delta,
            "target_row": target_binding,
            "plan_authority": {
                "canonical_authority": goal.get("canonical_authority"),
                "canonical_plan_sha256": goal.get("canonical_plan_sha256"),
                "executable_projection_sha256": goal.get("projection_sha256"),
                "row_start": goal.get("row_start"),
                "row_end": goal.get("row_end"),
                "task_count": goal.get("task_count"),
                "persistent_until": goal.get("persistent_until"),
                "superseded_history_count": len(superseded_task_ids),
                "superseded_excluded_from_current_projection": superseded_excluded,
            },
            "candidate_created": False,
            "pending_hil": False,
            "pointer_moved": False,
            "hil_inferred": False,
            "sealed_at": utc_now(),
        }
        receipt: dict[str, Any] = {
            **body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(body)),
        }
        session.metadata["task_classification_binding"] = receipt
        return receipt

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
        backlog_task_id: str | None = None,
        _native_route_receipt: dict[str, Any] | None = None,
        _installed_surface_inventory: dict[str, Any] | None = None,
        _project_panel_snapshot: dict[str, Any] | None = None,
        _task_checkpoint_proof: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        pointer = self.store.pointer(project_id)
        classification_reconciliation: dict[str, Any] | None = None
        active_backlog_task_id = str(
            session.metadata.get("active_backlog_task_id") or ""
        ).strip()
        prior_task_checkpoint_receipt = session.metadata.get(
            "last_task_checkpoint_advance"
        )
        task_checkpoint_replay_requested = (
            session.state == SessionState.TASK_CLASSIFIED
            and bool(backlog_task_id)
            and backlog_task_id == active_backlog_task_id
            and isinstance(prior_task_checkpoint_receipt, dict)
            and prior_task_checkpoint_receipt.get("replacement_backlog_task_id")
            == backlog_task_id
        )
        task_checkpoint_advance_requested = (
            session.state == SessionState.TASK_CLASSIFIED
            and bool(active_backlog_task_id)
            and bool(backlog_task_id)
            and backlog_task_id != active_backlog_task_id
            and isinstance(_task_checkpoint_proof, dict)
        )
        deterministic_task_id: str | None = None
        if task_checkpoint_advance_requested or task_checkpoint_replay_requested:
            deterministic_task_id = (
                "task_ck_"
                + sha256_bytes(
                    canonical_json_bytes(
                        {
                            "project_id": project_id,
                            "session_id": session_id,
                            "verification_proof_sha256": (
                                (_task_checkpoint_proof or {}).get("receipt_sha256")
                            ),
                            "replacement_backlog_task_id": backlog_task_id,
                        }
                    )
                )[:26].lower()
            )
        task = classify_task(
            task_class=task_class,
            requested_outcome=requested_outcome,
            permitted_paths=permitted_paths,
            permitted_tools=permitted_tools,
            acceptance_checks=acceptance_checks,
            stop_condition=stop_condition,
            task_id=deterministic_task_id,
        )
        task_checkpoint_advance: dict[str, Any] | None = None
        if task_checkpoint_replay_requested:
            task_checkpoint_advance = self._replay_task_checkpoint_advance(
                project_id,
                session_id,
                task=task,
                receipt=cast(dict[str, Any], prior_task_checkpoint_receipt),
                verification_proof=_task_checkpoint_proof,
                native_route_receipt=_native_route_receipt,
                installed_surface_inventory=_installed_surface_inventory,
                project_panel_snapshot=_project_panel_snapshot,
            )
            return {
                "status": "PASS",
                "session": session.as_dict(),
                "task": task.as_dict(),
                "classification_reconciliation": None,
                "task_checkpoint_advance": task_checkpoint_advance,
                "classification_binding": session.metadata.get(
                    "task_classification_binding"
                ),
            }
        if task_checkpoint_advance_requested:
            proof_schema = str((_task_checkpoint_proof or {}).get("schema") or "")
            verification_kind = (
                "EXACT_TASK_PROJECT_SESSION_BINDING"
                if proof_schema
                == "evidence-lane.codex-exact-task-project-session-binding.v1"
                else str((_task_checkpoint_proof or {}).get("verification_kind") or "")
            )
            task_checkpoint_advance = self._verify_task_checkpoint_advance(
                project_id,
                session_id,
                completed_backlog_task_id=active_backlog_task_id,
                replacement_backlog_task_id=cast(str, backlog_task_id),
                replacement_task=task,
                verification_kind=verification_kind,
                verification_proof=_task_checkpoint_proof,
                native_route_receipt=_native_route_receipt,
                installed_surface_inventory=_installed_surface_inventory,
                project_panel_snapshot=_project_panel_snapshot,
            )
        if (
            session.state == SessionState.TASK_CLASSIFIED
            and backlog_task_id
            and not task_checkpoint_advance_requested
        ):
            backlog = self.store.backlog_status(project_id)
            first_queued = next(
                (
                    row
                    for row in sorted(
                        backlog["tasks"], key=lambda item: int(item["sequence"])
                    )
                    if str(row.get("status")) == "QUEUED"
                ),
                None,
            )
            exact_old_task = session.task
            exact_completion_receipt = str(
                session.metadata.get("batch_completion_receipt_id") or ""
            ).strip()
            batch_receipt = self.store.batch_completion_receipt(
                project_id,
                exact_completion_receipt,
            )
            batch_receipt_body = (
                {
                    key: value
                    for key, value in batch_receipt.items()
                    if key != "receipt_sha256"
                }
                if isinstance(batch_receipt, dict)
                else {}
            )
            batch_receipt_valid = (
                isinstance(batch_receipt, dict)
                and batch_receipt.get("session_id") == session_id
                and batch_receipt.get("resulting_status") == "DONE_PENDING_HIL"
                and batch_receipt.get("candidate_accepted") is False
                and batch_receipt.get("hil_approval_inferred") is False
                and batch_receipt.get("receipt_sha256")
                == sha256_bytes(canonical_json_bytes(batch_receipt_body))
            )
            decisions = session.metadata.get("decisions")
            last_decision = (
                decisions[-1]
                if isinstance(decisions, list)
                and decisions
                and isinstance(decisions[-1], dict)
                else None
            )
            resumed_from_pending = session.metadata.get("resumed_from_pending")
            task_source_basis = session.metadata.get("task_source_basis")
            last_backlog_outcome = session.metadata.get("last_backlog_outcome")
            completed_backlog_row = next(
                (
                    row
                    for row in backlog["tasks"]
                    if isinstance(last_backlog_outcome, dict)
                    and row.get("task_id") == last_backlog_outcome.get("task_id")
                ),
                None,
            )
            completed_backlog_history = (
                completed_backlog_row.get("history", [])
                if isinstance(completed_backlog_row, dict)
                and isinstance(completed_backlog_row.get("history"), list)
                else []
            )
            correction_decision_id = str(
                (last_decision or {}).get("decision_id") or ""
            ).strip()
            correction_receipt: dict[str, Any] | None = None
            if correction_decision_id:
                correction_receipt_path = (
                    self.store.project_root(project_id)
                    / "receipts"
                    / f"{correction_decision_id}.json"
                )
                if correction_receipt_path.is_file():
                    try:
                        loaded_correction_receipt = json.loads(
                            correction_receipt_path.read_text(encoding="utf-8")
                        )
                    except (OSError, ValueError, TypeError):
                        loaded_correction_receipt = None
                    if isinstance(loaded_correction_receipt, dict):
                        correction_receipt = loaded_correction_receipt
            correction_candidate_validation: dict[str, Any] | None = None
            correction_candidate_id = str(
                (last_decision or {}).get("candidate_id") or ""
            ).strip()
            if correction_candidate_id:
                try:
                    correction_candidate_validation = self.store.candidate_validation(
                        project_id,
                        correction_candidate_id,
                        require_promotable=False,
                    )
                except (EvidenceLaneError, OSError, ValueError, TypeError):
                    correction_candidate_validation = None
            batch_completion_checks = {
                "batch_status_done_pending_hil": session.metadata.get(
                    "batch_backlog_task_status"
                )
                == "DONE_PENDING_HIL",
                "batch_receipt_id_present": bool(exact_completion_receipt),
                "batch_receipt_verified": batch_receipt_valid,
            }
            single_correction_checks = {
                "single_batch_status_absent": not session.metadata.get(
                    "batch_backlog_task_status"
                ),
                "single_batch_receipt_absent": not exact_completion_receipt,
                "single_resumed_contract_present": isinstance(
                    resumed_from_pending, dict
                ),
                "single_resumed_kind_correction": (
                    isinstance(resumed_from_pending, dict)
                    and resumed_from_pending.get("kind") == "CORRECTION"
                ),
                "single_decision_receipt_present": isinstance(correction_receipt, dict),
                "single_decision_receipt_matches_session": (
                    isinstance(correction_receipt, dict)
                    and correction_receipt == last_decision
                ),
                "single_decision_is_approve_with_delta": (
                    isinstance(last_decision, dict)
                    and last_decision.get("decision") == "APPROVE_WITH_DELTA"
                ),
                "single_decision_matches_resumed_contract": (
                    isinstance(last_decision, dict)
                    and isinstance(resumed_from_pending, dict)
                    and last_decision.get("decision_id")
                    == resumed_from_pending.get("decision_id")
                    and last_decision.get("candidate_id")
                    == resumed_from_pending.get("source_candidate_id")
                    and last_decision.get("correction_delta")
                    == resumed_from_pending.get("requested_outcome")
                    and resumed_from_pending.get("accepted_pv_context")
                    == pointer.accepted_pv
                ),
                "single_current_task_matches_correction": (
                    isinstance(exact_old_task, dict)
                    and isinstance(resumed_from_pending, dict)
                    and exact_old_task.get("task_class") == TaskClass.FIX_BUG.value
                    and exact_old_task.get("task_class")
                    == resumed_from_pending.get("required_task_class")
                    and exact_old_task.get("requested_outcome")
                    == resumed_from_pending.get("requested_outcome")
                ),
                "single_source_basis_matches_candidate": (
                    isinstance(task_source_basis, dict)
                    and isinstance(last_decision, dict)
                    and task_source_basis.get("kind") == "HIL_CANDIDATE_SOURCE"
                    and task_source_basis.get("candidate_id")
                    == last_decision.get("candidate_id")
                    and task_source_basis.get("accepted_pv_context")
                    == pointer.accepted_pv
                ),
                "single_candidate_integrity_verified": (
                    isinstance(correction_candidate_validation, dict)
                    and isinstance(last_decision, dict)
                    and correction_candidate_validation.get("manifest_sha256")
                    == last_decision.get("candidate_manifest_sha256")
                    and correction_candidate_validation.get("package_sha256")
                    == last_decision.get("candidate_package_sha256")
                ),
                "single_pointer_retained_by_decision": (
                    isinstance(last_decision, dict)
                    and last_decision.get("accepted_pv_retained") == pointer.accepted_pv
                    and last_decision.get("pointer_generation_before")
                    == pointer.generation
                    and last_decision.get("pointer_generation_after")
                    == pointer.generation
                ),
                "single_backlog_outcome_done": (
                    isinstance(last_backlog_outcome, dict)
                    and last_backlog_outcome.get("decision") == "APPROVE_WITH_DELTA"
                    and last_backlog_outcome.get("status") == "DONE"
                    and isinstance(completed_backlog_row, dict)
                    and completed_backlog_row.get("status") == "DONE"
                ),
                "single_backlog_history_matches_decision": (
                    isinstance(completed_backlog_row, dict)
                    and isinstance(last_decision, dict)
                    and any(
                        isinstance(history_row, dict)
                        and history_row.get("event") == "HIL_DECISION"
                        and history_row.get("decision") == "APPROVE_WITH_DELTA"
                        and history_row.get("candidate_id")
                        == last_decision.get("candidate_id")
                        for history_row in completed_backlog_history
                    )
                ),
            }
            batch_completion_valid = all(batch_completion_checks.values())
            single_correction_valid = all(single_correction_checks.values())
            completion_basis_kind = (
                "SEALED_BATCH_COMPLETION"
                if batch_completion_valid
                else (
                    "SEALED_SINGLE_TASK_HIL_CORRECTION"
                    if single_correction_valid
                    else None
                )
            )
            exact_agent_id = str(session.metadata.get("agent_id") or "").strip()
            exact_host_session_id = str(
                session.metadata.get("current_host_session_id") or ""
            ).strip()
            runtime_status = self.runtime_activation.status()
            runtime_binding = next(
                (
                    row
                    for row in runtime_status.get("active_sessions", [])
                    if row.get("project_id") == project_id
                    and row.get("session_id") == session_id
                ),
                None,
            )
            reconciliation_checks = {
                "prior_task_present": isinstance(exact_old_task, dict),
                "candidate_absent": session.candidate_id is None,
                "pending_hil_absent": not session.metadata.get("pending_hil"),
                "pending_task_absent": not isinstance(
                    session.metadata.get("pending_task"), dict
                ),
                "active_backlog_binding_absent": not session.metadata.get(
                    "active_backlog_task_id"
                ),
                "batch_backlog_binding_absent": not session.metadata.get(
                    "batch_backlog_task_ids"
                ),
                "prior_active_status_done": session.metadata.get(
                    "active_backlog_task_status"
                )
                == "DONE",
                "completion_basis_verified": completion_basis_kind is not None,
                "agent_identity_present": bool(exact_agent_id),
                "host_session_identity_present": bool(exact_host_session_id),
                "runtime_binding_matches": isinstance(runtime_binding, dict)
                and exact_host_session_id
                in runtime_binding.get("host_session_ids", []),
                "plan_has_no_active_task": not backlog["active"],
                "replacement_is_first_queued": isinstance(first_queued, dict)
                and first_queued.get("task_id") == backlog_task_id,
                "accepted_pointer_present": pointer.accepted_pv is not None,
                "accepted_pv_matches": pointer.accepted_pv == session.accepted_pv,
                "pointer_generation_matches": (
                    pointer.generation == session.accepted_pointer_generation
                ),
            }
            reconcilable = all(reconciliation_checks.values())
            failed_completion_checks = []
            if completion_basis_kind is None:
                failed_completion_checks = [
                    *(
                        f"batch.{key}"
                        for key, passed in batch_completion_checks.items()
                        if not passed
                    ),
                    *(
                        f"single_correction.{key}"
                        for key, passed in single_correction_checks.items()
                        if not passed
                    ),
                ]
            require(
                reconcilable,
                "COMPLETED_TASK_RECONCILIATION_MISMATCH",
                "A stale classified task may be reconciled only when its sealed "
                "completion state, empty candidate/HIL boundary, accepted pointer, "
                "single-writer identity, and first queued Plan task all match.",
                status="MISMATCH",
                backlog_task_id=backlog_task_id,
                first_queued_task_id=(
                    first_queued.get("task_id")
                    if isinstance(first_queued, dict)
                    else None
                ),
                active_task_ids=[row["task_id"] for row in backlog["active"]],
                pointer=pointer.as_dict(),
                session_accepted_pv=session.accepted_pv,
                session_pointer_generation=session.accepted_pointer_generation,
                failed_checks=sorted(
                    [key for key, passed in reconciliation_checks.items() if not passed]
                    + failed_completion_checks
                ),
            )
            prior_task = cast(dict[str, Any], exact_old_task)
            config = self.store.config(project_id)
            source_identity = identity_json(
                inspect_repository(
                    config.repository_path,
                    expected_owner=config.expected_owner,
                    expected_name=config.expected_name,
                ),
                config.repository_path,
            )
            source_identity_sha256 = sha256_bytes(canonical_json_bytes(source_identity))
            prior = {
                "state": session.state.value,
                "task": exact_old_task,
                "run_id": session.metadata.get("run_id"),
                "active_backlog_task_status": session.metadata.get(
                    "active_backlog_task_status"
                ),
                "batch_backlog_task_status": session.metadata.get(
                    "batch_backlog_task_status"
                ),
                "batch_completion_receipt_id": exact_completion_receipt,
                "task_mode_binding": session.metadata.get("task_mode_binding"),
                "resumed_from_pending": resumed_from_pending,
                "task_source_basis": task_source_basis,
            }
            completion_basis_receipt_id = (
                exact_completion_receipt
                if completion_basis_kind == "SEALED_BATCH_COMPLETION"
                else correction_decision_id
            )
            completion_basis_sha256 = (
                str((batch_receipt or {}).get("receipt_sha256") or "")
                if completion_basis_kind == "SEALED_BATCH_COMPLETION"
                else (
                    sha256_bytes(canonical_json_bytes(correction_receipt))
                    if isinstance(correction_receipt, dict)
                    else ""
                )
            )
            reconciliation_body = {
                "schema": "evidence-lane.completed-task-reconciliation.v1",
                "project_id": project_id,
                "session_id": session_id,
                "prior_task_id": prior_task.get("task_id"),
                "prior_run_id": session.metadata.get("run_id"),
                "replacement_backlog_task_id": backlog_task_id,
                "accepted_pv": pointer.accepted_pv,
                "pointer_generation": pointer.generation,
                "source_identity_sha256": source_identity_sha256,
                "agent_id": exact_agent_id,
                "host_session_id": exact_host_session_id,
                "runtime_activation_generation": runtime_status.get("generation"),
                "batch_completion_receipt_id": exact_completion_receipt,
                "completion_basis_kind": completion_basis_kind,
                "completion_basis_receipt_id": completion_basis_receipt_id,
                "completion_basis_sha256": completion_basis_sha256,
                "completed_backlog_task_id": (
                    last_backlog_outcome.get("task_id")
                    if completion_basis_kind == "SEALED_SINGLE_TASK_HIL_CORRECTION"
                    and isinstance(last_backlog_outcome, dict)
                    else None
                ),
                "candidate_present": False,
                "pending_hil": False,
                "pointer_moved": False,
                "candidate_created": False,
                "hil_inferred": False,
                "reconciled_at": utc_now(),
            }
            classification_reconciliation = {
                **reconciliation_body,
                "receipt_sha256": sha256_bytes(
                    canonical_json_bytes(reconciliation_body)
                ),
            }
            session.metadata.setdefault("completed_runs", []).append(
                {
                    **prior,
                    "completion_disposition": (
                        "STALE_CLASSIFICATION_RECONCILED_WITHOUT_HIL"
                        if completion_basis_kind == "SEALED_BATCH_COMPLETION"
                        else "HIL_CORRECTION_RECONCILED_WITHOUT_CANDIDATE"
                    ),
                    "reconciliation_receipt_sha256": (
                        classification_reconciliation["receipt_sha256"]
                    ),
                }
            )
            session.metadata.setdefault("classification_reconciliations", []).append(
                classification_reconciliation
            )
            if completion_basis_kind == "SEALED_BATCH_COMPLETION":
                session.metadata["last_reconciled_batch_completion_receipt_id"] = (
                    exact_completion_receipt
                )
            else:
                session.metadata["last_reconciled_hil_correction_decision_id"] = (
                    correction_decision_id
                )
            session.task = None
            session.metadata.pop("run_id", None)
            session.metadata.pop("task_mode_binding", None)
            session.metadata.pop("active_backlog_task_status", None)
            session.metadata.pop("batch_backlog_task_status", None)
            session.metadata.pop("batch_completion_receipt_id", None)
            session.metadata.pop("resumed_from_pending", None)
            session.metadata["source_update_confirmed"] = False
            target_entry = (
                SessionState.PVN_ACCEPTED
                if pointer.accepted_pv == "PV1"
                else SessionState.PVN1_ACCEPTED
            )
            session.state = transition(
                session.state,
                LifecycleEvent.RECONCILE_COMPLETED_TASK,
                target_entry,
            )
        pending = session.metadata.get("pending_task")
        pending_state = session.state in {
            SessionState.CORRECTION_TASK_PENDING,
            SessionState.RESEARCH_TASK_PENDING,
        }
        require(
            task_checkpoint_advance is not None
            or (
                session.state
                in {
                    SessionState.BOOTED,
                    SessionState.PVN_ACCEPTED,
                    SessionState.PVN1_ACCEPTED,
                    SessionState.PVN1_ENTRY,
                    SessionState.CORRECTION_TASK_PENDING,
                    SessionState.RESEARCH_TASK_PENDING,
                }
                and (pointer.accepted_pv is not None or pending_state)
            ),
            "TASK_CLASSIFICATION_STATE_INVALID",
            "A task requires an accepted entry PV, except for the exact stored "
            "follow-up to an unaccepted initial PV1 candidate.",
            status="BLOCKED",
            state=session.state.value,
            accepted_pv=pointer.accepted_pv,
        )
        require(
            session.task is None or task_checkpoint_advance is not None,
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
            self._verify_repository_matches_identity(
                project_id,
                self.store.candidate_metadata(project_id, source_candidate_id)[
                    "project_identity"
                ],
                error_code="PENDING_CANDIDATE_SOURCE_MISMATCH",
                message=(
                    "The live source no longer matches the candidate bound to the "
                    "pending HIL task."
                ),
            )
        prior_task_checkpoint_run: dict[str, Any] | None = None
        if task_checkpoint_advance is not None:
            prior_task_checkpoint_run = {
                "state": session.state.value,
                "task": task_checkpoint_advance["prior_task"],
                "run_id": session.metadata.get("run_id"),
                "task_mode_binding": session.metadata.get("task_mode_binding"),
                "active_backlog_task_id": active_backlog_task_id,
                "active_backlog_task_status": session.metadata.get(
                    "active_backlog_task_status"
                ),
            }
            session.metadata.pop("task_mode_binding", None)
        active_mode_binding = session.metadata.get("active_mode_binding")
        if isinstance(active_mode_binding, dict):
            governance = active_mode_binding.get("mode_governance")
            require(
                isinstance(governance, dict),
                "ACTIVE_MODE_GOVERNANCE_MISSING",
                "The selected mode has no executable ENV/UOP governance contract.",
                status="MISMATCH",
            )
            validate_mode_governance_selection(cast(dict[str, Any], governance))
            task_mode_core = {
                "schema": "evidence-lane.task-mode-binding.v1",
                "task_id": task.task_id,
                "selected_mode_ids": list(active_mode_binding["selected_mode_ids"]),
                "mode_intersection": active_mode_binding["mode_intersection"],
                "canonical_lanes": list(active_mode_binding["canonical_lanes"]),
                "selection_source": active_mode_binding["selection_source"],
                "mode_governance": governance,
                "selection_receipt_sha256": active_mode_binding[
                    "binding_receipt_sha256"
                ],
                "bound_at_task_classification": utc_now(),
                "lifecycle_effect": "NONE",
                "candidate_created": False,
                "pointer_moved": False,
                "hil_approval_inferred": False,
            }
            task_mode_core["binding_receipt_sha256"] = sha256_bytes(
                canonical_json_bytes(task_mode_core)
            )
            session.metadata["task_mode_binding"] = task_mode_core
        mutating_classes = {
            TaskClass.MODIFY_CODE,
            TaskClass.FIX_BUG,
            TaskClass.ADD_BOUNDED_FEATURE,
            TaskClass.PREPARE_PATCH,
        }
        current_freshness = self._current_live_root_freshness(project_id)
        session.metadata["current_accepted_freshness"] = current_freshness
        require(
            pending_state
            or classification_reconciliation is not None
            or task_checkpoint_advance is not None
            or task.task_class not in mutating_classes
            or current_freshness.get("state") == "FRESH",
            "STALE_ENTRY_MUTATION_BLOCKED",
            "A modifying task cannot claim the rolled-back or stale accepted PV as "
            "its live source. Restore that exact source or use a bounded read/research "
            "task before Refresh.",
            status="STALE",
            accepted_pv=pointer.accepted_pv,
            session_entry_pv=session.metadata.get("entry_pv"),
            freshness=current_freshness,
        )
        if classification_reconciliation is not None:
            config = self.store.config(project_id)
            current_source_identity = identity_json(
                inspect_repository(
                    config.repository_path,
                    expected_owner=config.expected_owner,
                    expected_name=config.expected_name,
                ),
                config.repository_path,
            )
            require(
                sha256_bytes(canonical_json_bytes(current_source_identity))
                == classification_reconciliation["source_identity_sha256"],
                "COMPLETED_TASK_RECONCILIATION_SOURCE_CHANGED",
                "The source boundary changed during stale-task reconciliation.",
                status="STALE",
            )
        if backlog_task_id:
            if task_checkpoint_advance is not None:
                receipt = cast(dict[str, Any], task_checkpoint_advance["receipt"])
                claimed_advance = self.store.advance_verified_task_checkpoint(
                    project_id,
                    completed_backlog_task_id=active_backlog_task_id,
                    replacement_backlog_task_id=backlog_task_id,
                    session_id=session_id,
                    prior_runtime_task_id=str(receipt["prior_runtime_task_id"]),
                    replacement_contract=task.as_dict(),
                    completion_receipt=receipt,
                )
                task_checkpoint_advance["plan_transition"] = claimed_advance
                session.metadata["active_backlog_task_id"] = backlog_task_id
                session.metadata["active_backlog_task_status"] = "ACTIVE"
                session.metadata.setdefault("completed_runs", []).append(
                    {
                        **cast(dict[str, Any], prior_task_checkpoint_run),
                        "completion_disposition": (
                            "VERIFIED_TASK_CHECKPOINT_COMPLETED_WITHOUT_CANDIDATE"
                        ),
                        "task_checkpoint_advance_receipt_sha256": receipt[
                            "receipt_sha256"
                        ],
                    }
                )
                session.metadata.setdefault("task_checkpoint_advances", []).append(
                    receipt
                )
                session.metadata["last_task_checkpoint_advance"] = receipt
            else:
                claimed = self.store.claim_backlog_task(
                    project_id,
                    backlog_task_id=backlog_task_id,
                    session_id=session_id,
                    contract=task.as_dict(),
                )
                session.metadata["active_backlog_task_id"] = claimed["task_id"]
                session.metadata["active_backlog_task_status"] = "ACTIVE"
        session.task = task.as_dict()
        session.candidate_id = None
        target_state = (
            SessionState.AWAITING_USER_APPLY_COMMIT
            if session.metadata.get("client_source_edit_authority") == "USER_MEDIATED"
            else SessionState.TASK_CLASSIFIED
        )
        if task_checkpoint_advance is not None:
            require(
                target_state == SessionState.TASK_CLASSIFIED,
                "TASK_CHECKPOINT_ADVANCE_CLIENT_AUTHORITY_MISMATCH",
                "Verified checkpoint closeout requires direct client source authority.",
                status="MISMATCH",
            )
            session.state = transition(
                session.state,
                LifecycleEvent.ADVANCE_VERIFIED_TASK_CHECKPOINT,
                target_state,
            )
            session.metadata["run_id"] = (
                "run_ck_"
                + sha256_bytes(
                    canonical_json_bytes(
                        {
                            "project_id": project_id,
                            "session_id": session_id,
                            "task_id": task.task_id,
                        }
                    )
                )[:26].lower()
            )
        else:
            session.state = transition(
                session.state,
                LifecycleEvent.CLASSIFY_TASK,
                target_state,
            )
            session.metadata["run_id"] = prefixed_id("run")
        session.metadata["source_update_confirmed"] = False
        if task_checkpoint_advance is not None:
            receipt = cast(dict[str, Any], task_checkpoint_advance["receipt"])
            session.metadata["task_source_basis"] = {
                "kind": "VERIFIED_TASK_CHECKPOINT_ADVANCE",
                "accepted_pv": pointer.accepted_pv,
                "pointer_generation": pointer.generation,
                "completed_backlog_task_id": active_backlog_task_id,
                "replacement_backlog_task_id": backlog_task_id,
                "verification_kind": receipt["verification_kind"],
                "task_checkpoint_advance_receipt_sha256": receipt["receipt_sha256"],
                "verification_proof_sha256": receipt["verification_proof"][
                    "receipt_sha256"
                ],
            }
            session.metadata["source_state"] = "VERIFIED_TASK_CHECKPOINT_ADVANCE"
            session.metadata["accepted_pv_query_scope"] = "ENTRY_STATE_ONLY"
        elif classification_reconciliation is not None:
            session.metadata["task_source_basis"] = {
                "kind": "RECONCILED_UNFINISHED_SOURCE_BOUNDARY",
                "accepted_pv": pointer.accepted_pv,
                "pointer_generation": pointer.generation,
                "source_identity_sha256": classification_reconciliation[
                    "source_identity_sha256"
                ],
                "prior_task_id": classification_reconciliation["prior_task_id"],
                "reconciliation_receipt_sha256": classification_reconciliation[
                    "receipt_sha256"
                ],
            }
            session.metadata["source_state"] = "RECONCILED_UNFINISHED_SOURCE_BOUNDARY"
            session.metadata["accepted_pv_query_scope"] = "ENTRY_STATE_ONLY"
        elif pending_state:
            exact_pending = cast(dict[str, Any], pending)
            session.metadata["resumed_from_pending"] = exact_pending
            session.metadata.pop("pending_task", None)
            session.metadata["task_source_basis"] = {
                "kind": "HIL_CANDIDATE_SOURCE",
                "candidate_id": exact_pending["source_candidate_id"],
                "accepted_pv_context": pointer.accepted_pv,
            }
            session.metadata["source_state"] = "PENDING_CANDIDATE_SOURCE_EXACT"
            session.metadata["accepted_pv_query_scope"] = (
                "ENTRY_STATE_ONLY"
                if pointer.accepted_pv is not None
                else "NO_ACCEPTED_PV_PENDING_CANDIDATE_SOURCE_ONLY"
            )
        else:
            session.metadata["task_source_basis"] = {
                "kind": "ACCEPTED_PV_ENTRY",
                "accepted_pv": pointer.accepted_pv,
            }
        classification_binding = self._seal_task_classification_binding(
            project_id,
            session_id,
            session=session,
            task=task,
            pointer=pointer,
            target_backlog_task_id=backlog_task_id,
            prior_executable_task_id=(active_backlog_task_id or None),
        )
        self._save(session)
        task_checkpoint_lineage: dict[str, Any] | None = None
        if task_checkpoint_advance is not None:
            receipt = cast(dict[str, Any], task_checkpoint_advance["receipt"])
            task_checkpoint_lineage = self._append_task_checkpoint_advance_lineage(
                project_id,
                session_id,
                session=session,
                task=task,
                receipt=receipt,
            )
        if classification_reconciliation is not None:
            ChatLineage(self._lineage_path(project_id, session_id)).append(
                event_type="task.completed_classification.reconciled",
                visible_payload=classification_reconciliation,
                occurred_at=classification_reconciliation["reconciled_at"],
                session_id=session_id,
                task_id=classification_reconciliation["prior_task_id"],
                run_id=classification_reconciliation["prior_run_id"],
            )
        if task_checkpoint_advance is None:
            lineage_payload = task.as_dict()
            lineage_payload["classification_binding"] = classification_binding
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
        host_plan_rehydration = self._prepare_host_plan_rehydration(
            project_id,
            session,
            trigger="TASK_CLASSIFICATION_TRANSITION",
            trigger_event_id=str(classification_binding["receipt_sha256"]),
            host_goal_active=None,
        )
        if host_plan_rehydration is not None:
            session.metadata["last_host_plan_rehydration_receipt_sha256"] = cast(
                dict[str, Any], host_plan_rehydration["receipt"]
            )["receipt_sha256"]
            self._save(session)
        return {
            "status": "PASS",
            "session": session.as_dict(),
            "task": task.as_dict(),
            "classification_reconciliation": classification_reconciliation,
            "task_checkpoint_advance": task_checkpoint_advance,
            "task_checkpoint_lineage": task_checkpoint_lineage,
            "classification_binding": classification_binding,
            "host_plan_rehydration": host_plan_rehydration,
        }

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
                # A failed candidate build retains the exact classified task
                # in EXIT_BUILDING for bounded recovery.  Visible operational
                # prompt/tool/error capture must remain available while no HIL
                # or pointer effect has been produced.
                SessionState.EXIT_BUILDING,
                # A HIL-owning Delta remains the sole ACTIVE Plan task while
                # its unaccepted candidate is presented. Visible verification,
                # warning, and correction evidence must remain appendable
                # through that human-decision boundary.
                SessionState.PV1_CANDIDATE,
                SessionState.PVN1_CANDIDATE,
            },
            "TASK_ACTIVITY_STATE_INVALID",
            "Visible task activity requires one active classified task.",
            status="BLOCKED",
        )
        allowed = {
            "prompt",
            "steer",
            "tool.selected",
            "command.executed",
            "git.fast_forward",
            "git.fast_forward.noop",
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
            "host.plan.observation",
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
        current_host_session_id = str(
            session.metadata.get("current_host_session_id") or ""
        ).strip()
        execution_profile_value = session.metadata.get("execution_profile")
        execution_profile: dict[str, Any] = (
            cast(dict[str, Any], execution_profile_value)
            if isinstance(execution_profile_value, dict)
            else {}
        )
        event_payload.setdefault(
            "host_identity",
            {
                "host_kind": session.host.value,
                "host_profile": str(
                    execution_profile.get("host_profile") or "UNAVAILABLE"
                ),
                "host_session_id_sha256": (
                    sha256_bytes(current_host_session_id.encode("utf-8"))
                    if current_host_session_id
                    else None
                ),
                "raw_host_session_id_stored": False,
            },
        )
        if activity_type in {
            "git.fast_forward",
            "file.created",
            "file.modified",
            "file.deleted",
        }:
            if "first_source_mutation_at" not in session.metadata:
                session.metadata["first_source_mutation_at"] = utc_now()
            session.metadata["source_state"] = "MUTATED_AFTER_ENTRY"
            session.metadata["accepted_pv_query_scope"] = "ENTRY_STATE_ONLY"
            event_payload["source_state_after_activity"] = "MUTATED_AFTER_ENTRY"
            event_payload["accepted_pv_query_scope"] = "ENTRY_STATE_ONLY"
            self._save(session)
        steer_active_plan: dict[str, Any] | None = None
        if activity_type == "steer":
            backlog_status = self.store.backlog_status(project_id)
            goal_projection = cast(
                dict[str, Any], backlog_status.get("goal_projection") or {}
            )
            active_rows = [
                dict(row)
                for row in goal_projection.get("rows") or []
                if isinstance(row, dict)
                and str(row.get("status") or "").lower() == "in_progress"
                and str(row.get("lifecycle_status") or "").upper() == "ACTIVE"
            ]
            require(
                len(active_rows) == 1,
                "CANON_RUNTIME_ACTIVE_PLAN_BINDING_INVALID",
                "A visible steer requires exactly one canonical active Plan row.",
                status="MISMATCH",
                active_rows=len(active_rows),
            )
            steer_active_plan = active_rows[0]
            steer_active_plan.update(
                {
                    "canonical_plan_sha256": goal_projection.get(
                        "canonical_plan_sha256"
                    ),
                    "goal_projection_sha256": goal_projection.get("projection_sha256"),
                    "event_head_sha256": backlog_status.get("event_head_sha256"),
                }
            )
        lineage = ChatLineage(self._lineage_path(project_id, session_id))
        existing_event = (
            next(
                (row for row in lineage.events() if row.get("event_id") == event_id),
                None,
            )
            if event_id is not None
            else None
        )
        occurred_at = (
            str(existing_event["occurred_at"])
            if existing_event is not None
            else utc_now()
        )
        event = lineage.append(
            event_type=f"task.{activity_type}",
            visible_payload=event_payload,
            occurred_at=occurred_at,
            session_id=session_id,
            task_id=task_payload["task_id"],
            run_id=session.metadata["run_id"],
            event_id=event_id,
            model=str(execution_profile.get("model") or "") or None,
            submodel=str(execution_profile.get("submodel") or "") or None,
            token_metrics=(
                dict(visible_payload.get("token_metrics") or {})
                if isinstance(visible_payload.get("token_metrics"), dict)
                else None
            ),
        )
        observed_experience: dict[str, Any] | None = None
        host_plan_rehydration: dict[str, Any] | None = None
        if steer_active_plan is not None:
            pointer = self.store.pointer(project_id)
            observed_experience = seal_observed_experience_packet(
                self.store.project_root(project_id),
                project_id=project_id,
                evidence_session_id=session_id,
                runtime_task_id=str(task_payload["task_id"]),
                plan_task_id=str(steer_active_plan["task_id"]),
                active_plan=steer_active_plan,
                event=event,
                expected_accepted_pv=str(pointer.accepted_pv or ""),
                expected_pointer_generation=pointer.generation,
                input_kind="steer",
            )
        if activity_type == "host.plan.observation":
            observed_artifact_value = visible_payload.get("observed_artifact")
            require(
                isinstance(observed_artifact_value, dict),
                "HOST_PLAN_OBSERVATION_PAYLOAD_REQUIRED",
                "A host Plan observation activity requires one observed_artifact object.",
                status="BLOCKED",
            )
            host_plan_rehydration = self._prepare_host_plan_rehydration(
                project_id,
                session,
                trigger=str(
                    visible_payload.get("trigger") or "EXPLICIT_HOST_OBSERVATION"
                ),
                trigger_event_id=str(event["event_id"]),
                observed_artifact=cast(dict[str, Any], observed_artifact_value),
                host_capability=str(
                    visible_payload.get("host_capability") or "SUPPORTED"
                ),
                host_goal_active=(
                    bool(visible_payload["host_goal_active"])
                    if "host_goal_active" in visible_payload
                    else None
                ),
            )
            require(
                host_plan_rehydration is not None,
                "HOST_PLAN_REHYDRATION_NOT_APPLICABLE",
                "The current governed Plan has no physically final HIL projection.",
                status="BLOCKED",
            )
        return {
            "status": "PASS",
            "event": event,
            "observed_experience": observed_experience,
            "host_plan_rehydration": host_plan_rehydration,
            "source_state": session.metadata.get("source_state"),
            "accepted_pv_query_scope": session.metadata.get("accepted_pv_query_scope"),
        }

    def record_mode_classification(
        self,
        project_id: str,
        session_id: str,
        *,
        classification: dict[str, Any],
    ) -> dict[str, Any]:
        """Append one mode/lane route receipt without changing lifecycle state."""

        session = self.load(project_id, session_id)
        require(
            not session.metadata.get("closed_at"),
            "MODE_CLASSIFICATION_SESSION_CLOSED",
            "An operating-mode receipt cannot append to a closed session.",
            status="BLOCKED",
        )
        pointer = self.store.pointer(project_id)
        governance_value = classification.get("mode_governance")
        require(
            isinstance(governance_value, dict),
            "MODE_GOVERNANCE_SELECTION_REQUIRED",
            "Mode classification requires its ENV/UOP governance selection.",
            status="MISMATCH",
        )
        governance = validate_mode_governance_selection(
            cast(dict[str, Any], governance_value)
        )
        request_sha256 = sha256_bytes(
            str(classification.get("request", "")).encode("utf-8")
        )
        binding_core = {
            "schema": "evidence-lane.active-mode-binding.v1",
            "request_sha256": request_sha256,
            "selection_source": governance["selection_source"],
            "selected_mode_ids": [
                item["id"] for item in classification["selected_modes"]
            ],
            "mode_intersection": classification["mode_intersection"],
            "canonical_lanes": classification["canonical_lanes"],
            "mode_governance": governance,
            "selected_at": utc_now(),
            "lifecycle_state": session.state.value,
            "active_task_id": (
                cast(dict[str, Any], session.task).get("task_id")
                if session.task
                else None
            ),
            "candidate_created": False,
            "pointer_moved": False,
            "hil_approval_inferred": False,
        }
        binding_core["binding_receipt_sha256"] = sha256_bytes(
            canonical_json_bytes(binding_core)
        )
        session.metadata["active_mode_binding"] = binding_core
        session.metadata.setdefault("mode_binding_history", []).append(binding_core)
        if session.task and session.state in {
            SessionState.TASK_CLASSIFIED,
            SessionState.AWAITING_USER_APPLY_COMMIT,
        }:
            task_id = str(cast(dict[str, Any], session.task)["task_id"])
            task_mode_core = {
                "schema": "evidence-lane.task-mode-binding.v1",
                "task_id": task_id,
                "selected_mode_ids": list(binding_core["selected_mode_ids"]),
                "mode_intersection": binding_core["mode_intersection"],
                "canonical_lanes": list(binding_core["canonical_lanes"]),
                "selection_source": binding_core["selection_source"],
                "mode_governance": governance,
                "selection_receipt_sha256": binding_core["binding_receipt_sha256"],
                "bound_at_task_classification": binding_core["selected_at"],
                "lifecycle_effect": "NONE",
                "candidate_created": False,
                "pointer_moved": False,
                "hil_approval_inferred": False,
            }
            task_mode_core["binding_receipt_sha256"] = sha256_bytes(
                canonical_json_bytes(task_mode_core)
            )
            session.metadata["task_mode_binding"] = task_mode_core
        self._save(session)
        payload = {
            "schema": "evidence-lane.mode-classification-receipt.v1",
            "request_sha256": request_sha256,
            "mode_namespace_authority": classification["mode_namespace_authority"],
            "selected_mode_ids": [
                item["id"] for item in classification["selected_modes"]
            ],
            "mode_intersection": classification["mode_intersection"],
            "canonical_lanes": classification["canonical_lanes"],
            "visible_formula_response": governance["visible_formula_response"],
            "combined_operator_receipt_sha256": governance[
                "combined_operator_receipt_sha256"
            ],
            "lane_hil_contracts": [
                contract["hil"] for contract in governance["contracts"]
            ],
            "binding_receipt_sha256": binding_core["binding_receipt_sha256"],
            "chat_lineage_included": True,
            "lifecycle_state_before": session.state.value,
            "accepted_pv": pointer.accepted_pv,
            "pointer_generation": pointer.generation,
            "pointer_moved": False,
            "candidate_created": False,
            "task_classified": False,
            "private_reasoning_excluded": True,
        }
        event = ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="mode.classified",
            visible_payload=payload,
            occurred_at=utc_now(),
            session_id=session_id,
            task_id=(
                cast(dict[str, Any], session.task).get("task_id")
                if session.task
                else None
            ),
            run_id=session.metadata.get("run_id"),
        )
        return {
            "status": "PASS",
            "event": event,
            "mode_binding": binding_core,
            "lifecycle_state_unchanged": session.state.value,
            "pointer": pointer.as_dict(),
        }

    def record_hil_intent(
        self,
        project_id: str,
        session_id: str,
        *,
        classification: dict[str, Any],
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """Append visible HIL intent without deciding or moving a pointer."""

        session = self.load(project_id, session_id)
        require(
            not session.metadata.get("closed_at"),
            "HIL_INTENT_SESSION_CLOSED",
            "HIL intent cannot append to a closed governed session.",
            status="BLOCKED",
        )
        pointer = self.store.pointer(project_id)
        payload = {
            **classification,
            "lifecycle_state": session.state.value,
            "accepted_pv": pointer.accepted_pv,
            "pointer_generation": pointer.generation,
            "pointer_moved": False,
            "candidate_promoted": False,
            "private_reasoning_excluded": True,
        }
        event = ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="hil.intent.classified",
            visible_payload=payload,
            occurred_at=utc_now(),
            session_id=session_id,
            task_id=(
                cast(dict[str, Any], session.task).get("task_id")
                if session.task
                else None
            ),
            run_id=session.metadata.get("run_id"),
            event_id=event_id,
            actor_type="user",
        )
        return {"status": "PASS", "event": event, "pointer": pointer.as_dict()}

    def record_source_intake_classification(
        self,
        project_id: str,
        session_id: str,
        *,
        classification: dict[str, Any],
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """Append one visible source-intake receipt without changing lifecycle state."""

        session = self.load(project_id, session_id)
        require(
            not session.metadata.get("closed_at"),
            "SOURCE_INTAKE_SESSION_CLOSED",
            "Source Intake cannot append to a closed governed session.",
            status="BLOCKED",
        )
        pointer = self.store.pointer(project_id)
        lineage = ChatLineage(self._lineage_path(project_id, session_id))
        existing_event = (
            next(
                (row for row in lineage.events() if row.get("event_id") == event_id),
                None,
            )
            if event_id is not None
            else None
        )
        occurred_at = (
            str(existing_event["occurred_at"])
            if existing_event is not None
            else utc_now()
        )
        authority = classification.get("source_authority")
        event = lineage.append(
            event_type="source.intake.classified",
            visible_payload={
                **classification,
                "lifecycle_state_before": session.state.value,
                "accepted_pv": pointer.accepted_pv,
                "pointer_generation": pointer.generation,
                "private_reasoning_excluded": True,
            },
            occurred_at=occurred_at,
            session_id=session_id,
            task_id=(
                cast(dict[str, Any], session.task).get("task_id")
                if session.task
                else None
            ),
            run_id=session.metadata.get("run_id"),
            event_id=event_id,
            actor_type="user",
        )
        session.metadata["git_arm_mode"] = classification["git_optional_arm"][
            "requested_mode"
        ]
        if (
            classification.get("authority_mode") == "GOVERNED_CONTENT_REGISTRY"
            and isinstance(authority, dict)
            and authority.get("batch_id")
        ):
            session.metadata["classified_source_authority"] = {
                "batch_id": authority["batch_id"],
                "batch_sha256": authority["batch_sha256"],
                "source_count": authority["source_count"],
                "classified_at_lifecycle_state": session.state.value,
                "armed_for_candidate": False,
            }
        self._save(session)
        return {
            "status": "PASS",
            "event": event,
            "lifecycle_state_unchanged": session.state.value,
            "pointer": pointer.as_dict(),
            "source_authority": session.metadata.get("classified_source_authority"),
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
        required = "HOST_SANDBOX_FINAL_STATE_CONFIRMED"
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

    def refresh_exit(
        self,
        project_id: str,
        session_id: str,
        *,
        batch_task_evidence: list[dict[str, Any]] | None = None,
        batch_completion_confirmation: str | None = None,
    ) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        recovering_interrupted_exit = session.state == SessionState.EXIT_BUILDING
        require(
            session.task is not None
            and session.state
            in {
                SessionState.TASK_CLASSIFIED,
                SessionState.AWAITING_USER_APPLY_COMMIT,
                SessionState.EXIT_BUILDING,
            },
            "REFRESH_STATE_INVALID",
            "PV Refresh requires one active classified task or an interrupted exit "
            "with no sealed candidate.",
            status="BLOCKED",
            state=session.state.value,
        )
        if recovering_interrupted_exit:
            require(
                session.candidate_id is None,
                "INTERRUPTED_EXIT_ALREADY_SEALED",
                "An interrupted exit may be retried only when no candidate was sealed.",
                status="BLOCKED",
                candidate_id=session.candidate_id,
            )
        require(
            bool(session.metadata.get("source_update_confirmed")),
            "SOURCE_UPDATE_NOT_CONFIRMED",
            "The host must explicitly confirm its final source state before Refresh.",
            status="BLOCKED",
        )
        batch_requested = (
            batch_task_evidence is not None or batch_completion_confirmation is not None
        )
        batch_preflight: dict[str, Any] | None = None
        if batch_requested:
            require(
                batch_task_evidence is not None
                and batch_completion_confirmation is not None
                and not session.metadata.get("active_backlog_task_id"),
                "BATCH_DELTA_REFRESH_ARGUMENTS_INVALID",
                "Batch completion requires both exact evidence and confirmation and cannot replace one claimed Delta.",
                status="BLOCKED",
            )
            exact_batch_evidence = cast(list[dict[str, Any]], batch_task_evidence)
            exact_batch_confirmation = cast(str, batch_completion_confirmation)
            batch_preflight = self.store.validate_backlog_batch_completion(
                project_id,
                task_evidence=exact_batch_evidence,
                confirmation=exact_batch_confirmation,
            )
        task_payload = cast(dict[str, Any], session.task)
        recovery_receipt: dict[str, Any] | None = None
        if recovering_interrupted_exit:
            session.state = transition(
                session.state,
                LifecycleEvent.RECOVER_INTERRUPTED_EXIT,
                SessionState.EXIT_BUILDING,
            )
            recovery_receipt = {
                "schema": "evidence-lane.interrupted-exit-recovery.v1",
                "session_id": session_id,
                "run_id": session.metadata.get("run_id"),
                "candidate_absent": True,
                "accepted_pv": session.accepted_pv,
                "pointer_generation": session.accepted_pointer_generation,
                "recovered_at": utc_now(),
                "pointer_moved": False,
                "acceptance_inferred": False,
            }
            session.metadata.setdefault("interrupted_exit_recoveries", []).append(
                recovery_receipt
            )
        else:
            session.state = transition(
                session.state,
                LifecycleEvent.BEGIN_EXIT,
                SessionState.EXIT_BUILDING,
            )
        self._save(session)
        if recovery_receipt is not None:
            ChatLineage(self._lineage_path(project_id, session_id)).append(
                event_type="pv.interrupted_exit.recovered",
                visible_payload=recovery_receipt,
                occurred_at=cast(str, recovery_receipt["recovered_at"]),
                session_id=session_id,
                task_id=cast(dict[str, Any], session.task)["task_id"],
                run_id=cast(str, session.metadata["run_id"]),
            )
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
        pointer = self.store.pointer(project_id)
        task_source_basis = session.metadata.get("task_source_basis")
        initial_retry = bool(
            pointer.accepted_pv is None
            and isinstance(task_source_basis, dict)
            and task_source_basis.get("kind") == "HIL_CANDIDATE_SOURCE"
        )
        result = self.engine.build_candidate(
            project_id=project_id,
            session=session,
            run_id=session.metadata["run_id"],
            lineage_path=self._lineage_path(project_id, session_id),
            task=task,
            initial_entry=initial_retry,
        )
        self._consume_lane_route_grant(session, result["candidate_id"])
        session.candidate_id = result["candidate_id"]
        candidate_state = (
            SessionState.PV1_CANDIDATE if initial_retry else SessionState.PVN1_CANDIDATE
        )
        session.state = transition(
            session.state,
            (
                LifecycleEvent.SEAL_INITIAL_RETRY
                if initial_retry
                else LifecycleEvent.SEAL_EXIT
            ),
            candidate_state,
        )
        session.metadata["candidate_pointer_generation"] = (
            session.accepted_pointer_generation
        )
        session.metadata["source_state"] = "REFRESH_CANDIDATE_BUILT_FROM_FINAL_SOURCE"
        session.metadata["accepted_pv_query_scope"] = "ENTRY_STATE_ONLY_UNTIL_APPROVE"
        self._save(session)
        ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type=(
                "pv.initial_retry_candidate.created"
                if initial_retry
                else "pv.refresh_candidate.created"
            ),
            visible_payload={
                "candidate_id": result["candidate_id"],
                "proposed_pv": result["proposed_pv"],
                "manifest_sha256": result["manifest_sha256"],
                "source_delta": result["source_delta"],
                "warnings": result["warnings"],
                "initial_pv_retry": initial_retry,
            },
            occurred_at=utc_now(),
            session_id=session_id,
            task_id=task.task_id,
            run_id=session.metadata["run_id"],
        )
        backlog_done: dict[str, Any] | None = None
        batch_backlog_done: dict[str, Any] | None = None
        backlog_task_id = session.metadata.get("active_backlog_task_id")
        if backlog_task_id:
            # Sealing an unaccepted candidate is not task completion.  The
            # exact row remains ACTIVE until the user records the HIL outcome;
            # record_backlog_outcome performs ACTIVE -> DONE -> outcome only
            # after that explicit decision.
            active_rows = self.store.backlog_status(project_id)["active"]
            backlog_done = next(
                (row for row in active_rows if row.get("task_id") == backlog_task_id),
                None,
            )
            require(
                backlog_done is not None,
                "REFRESH_ACTIVE_BACKLOG_TASK_MISMATCH",
                "Refresh must preserve the exact active Delta through HIL approval.",
                status="MISMATCH",
                active_backlog_task_id=backlog_task_id,
            )
            session.metadata["active_backlog_task_status"] = "ACTIVE"
            self._save(session)
        elif batch_requested:
            batch_backlog_done = self.store.record_backlog_batch_done(
                project_id,
                session_id=session_id,
                candidate_id=result["candidate_id"],
                task_evidence=cast(list[dict[str, Any]], batch_task_evidence),
                confirmation=cast(str, batch_completion_confirmation),
            )
            session.metadata["batch_backlog_task_ids"] = batch_backlog_done["task_ids"]
            session.metadata["batch_completion_receipt_id"] = batch_backlog_done[
                "receipt_id"
            ]
            session.metadata["batch_backlog_task_status"] = "DONE_PENDING_HIL"
            self._save(session)
        finalized_overlay = self.store.finalize_candidate_overlay(
            project_id, result["candidate_id"]
        )
        result["stored_validation"] = finalized_overlay
        return {
            "status": "PASS",
            "session": session.as_dict(),
            "candidate": result,
            "interrupted_exit_recovery": recovery_receipt,
            "backlog_task": backlog_done,
            "batch_backlog_preflight": batch_preflight,
            "batch_backlog_completion": batch_backlog_done,
        }

    def reconcile_pending_hil_candidate_after_lifecycle_append(
        self,
        project_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Reseal one unchanged candidate ID after bounded exit-side writes."""

        session = self.load(project_id, session_id)
        require(
            session.task is not None
            and session.candidate_id is not None
            and session.state
            in {SessionState.PV1_CANDIDATE, SessionState.PVN1_CANDIDATE}
            and session.metadata.get("source_update_confirmed") is True,
            "PENDING_HIL_CANDIDATE_RECONCILIATION_STATE_INVALID",
            "Candidate reconciliation requires the exact pending HIL candidate and confirmed final source.",
            status="MISMATCH",
            state=session.state.value,
            candidate_id=session.candidate_id,
        )
        candidate_id = str(session.candidate_id)
        active_backlog_task_id = str(
            session.metadata.get("active_backlog_task_id") or ""
        ).strip()
        sub_pv_reconciliation = (
            self.store.reconcile_verified_predecessor_sub_pv(
                project_id,
                active_task_id=active_backlog_task_id,
                session_id=session_id,
            )
            if active_backlog_task_id
            else {
                "status": "PASS",
                "state": "NOT_APPLICABLE_NO_ACTIVE_PLAN_ROW",
                "sub_pv_acceptance": None,
                "plan_task_advanced": False,
                "project_pointer_moved": False,
            }
        )
        project_overlay_reconciliation = (
            self.store.reconcile_pending_hil_project_overlay(
                project_id,
                candidate_id,
                session_id=session_id,
            )
            if self.store.project_root(project_id)
            != self.store._legacy_project_root(project_id)
            else {
                "status": "PASS",
                "state": "NOT_APPLICABLE_LEGACY_PROJECT_AUTHORITY",
                "project_overlay_refreshed": False,
                "candidate_rebuilt": False,
                "pointer_moved": False,
            }
        )
        receipt_path = self.store._candidate_overlay_receipt_path(
            project_id, candidate_id
        )
        before_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        before_sha256 = str(before_receipt.get("receipt_sha256") or "")
        task = cast(dict[str, Any], session.task)
        event = ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="pv.candidate.overlay_reconciliation_started",
            visible_payload={
                "candidate_id": candidate_id,
                "prior_overlay_receipt_sha256": before_sha256,
                "reason": "FINALIZE_AFTER_ALL_DELTA_EXIT_AND_HIL_SIDE_WRITES",
                "candidate_rebuilt": False,
                "candidate_history_preserved": True,
                "predecessor_sub_pv_reconciliation": sub_pv_reconciliation.get(
                    "state"
                ),
                "project_overlay_reconciliation_transition_id": (
                    project_overlay_reconciliation.get("transition_id")
                ),
                "project_overlay_refreshed": project_overlay_reconciliation.get(
                    "project_overlay_refreshed"
                ),
                "pointer_moved": False,
                "hil_decision_recorded": False,
            },
            occurred_at=utc_now(),
            session_id=session_id,
            task_id=str(task["task_id"]),
            run_id=cast(str, session.metadata["run_id"]),
            event_id=f"candidate-overlay-reconcile-{before_sha256[:24].lower()}",
        )
        finalized = self.store.finalize_candidate_overlay(project_id, candidate_id)
        after_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        require(
            after_receipt.get("candidate_id") == candidate_id
            and after_receipt.get("candidate_id_preserved") is True
            and after_receipt.get("candidate_rebuilt") is False
            and after_receipt.get("candidate_history_preserved") is True
            and after_receipt.get("prior_overlay_receipt_sha256") == before_sha256,
            "PENDING_HIL_CANDIDATE_RECONCILIATION_POSTCONDITION_FAILED",
            "The pending candidate was not resealed through the append-only preservation route.",
            status="FAIL",
            candidate_id=candidate_id,
        )
        metadata = self.store.candidate_metadata(project_id, candidate_id)
        validation = dict(finalized)
        return {
            "status": "PASS",
            "candidate": {
                "candidate_id": candidate_id,
                "proposed_pv": validation.get("proposed_pv"),
                "manifest_sha256": validation.get("manifest_sha256"),
                "package_sha256": validation.get("package_sha256"),
                "next_action": "PRESENT_SIX_WAY_HIL",
                "stored_validation": validation,
                "candidate_id_preserved": True,
                "candidate_rebuilt": False,
                "candidate_history_preserved": True,
                "accepted_archive_opened": False,
                "accepted_archive_queried": False,
            },
            "candidate_overlay_reconciliation": {
                "prior_receipt_sha256": before_sha256,
                "current_receipt_sha256": after_receipt["receipt_sha256"],
                "revision_path": str(
                    self.store.project_root(project_id)
                    / "receipts"
                    / "candidate-overlay-revisions"
                    / candidate_id
                    / f"{before_sha256}.json"
                ),
                "event": event,
            },
            "predecessor_sub_pv_reconciliation": sub_pv_reconciliation,
            "project_overlay_reconciliation": project_overlay_reconciliation,
            "package_metadata": metadata,
            "session": session.as_dict(),
            "pointer": self.store.pointer(project_id).as_dict(),
        }

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
        rollback_to: str | None = None,
        decision_id: str | None = None,
        dual_learning_hil: dict[str, Any] | None = None,
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
        pointer_moved = False
        dual_hil_plan_stamp: dict[str, Any] | None = None
        if outcome == HilDecision.APPROVE:
            require(
                not correction_delta and not research_question and not rollback_to,
                "APPROVE_FIELDS_INVALID",
                "APPROVE may not include correction, research, or rollback payloads.",
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
            pointer_moved = True
            session.accepted_pv = after.accepted_pv
            session.accepted_pointer_generation = after.generation
            target_state = (
                SessionState.PVN_ACCEPTED
                if after.accepted_pv == "PV1"
                else SessionState.PVN1_ACCEPTED
            )
            session.state = transition(
                session.state,
                LifecycleEvent.HIL_APPROVE,
                target_state,
            )
            session.metadata["source_state"] = "ACCEPTED_ENTRY_EXACT"
            session.metadata["accepted_pv_query_scope"] = "CURRENT_ENTRY_STATE"
            session.metadata["current_accepted_freshness"] = (
                self._current_live_root_freshness(project_id)
            )
            decision_receipt = result["receipt"]
            if dual_learning_hil is not None:
                exact_dual = dict(dual_learning_hil)
                require(
                    exact_dual.get("status") == "PASS"
                    and exact_dual.get("required") is True
                    and exact_dual.get("project_target_pv") == after.accepted_pv
                    and bool(
                        exact_dual.get("learning_approval_receipt_sha256")
                    ),
                    "DUAL_HIL_PLAN_STAMP_BINDING_INVALID",
                    "The Project and Learning approval receipts must target the same PV before Plan stamping.",
                    status="MISMATCH",
                )
                stamp_body = {
                    "schema": "evidence-lane.plan-dual-hil-acceptance-stamp.v1",
                    "status": "PASS",
                    "project_id": project_id,
                    "plan_task_id": decision_task_id,
                    "target_pv": after.accepted_pv,
                    "project_decision": "APPROVE",
                    "project_decision_id": exact_decision_id,
                    "project_decision_receipt_sha256": decision_receipt.get(
                        "receipt_sha256"
                    ),
                    "project_proposal_id": candidate_id,
                    "learning_decision": "APPROVE",
                    "learning_weave_candidate_id": exact_dual[
                        "learning_weave_candidate_id"
                    ],
                    "learning_weave_candidate_sha256": exact_dual[
                        "learning_weave_candidate_sha256"
                    ],
                    "learning_weave_receipt_sha256": exact_dual[
                        "learning_weave_receipt_sha256"
                    ],
                    "learning_approval_receipt_sha256": exact_dual[
                        "learning_approval_receipt_sha256"
                    ],
                    "learning_member_count": exact_dual[
                        "learning_member_count"
                    ],
                    "learning_summary": (
                        f"{exact_dual['learning_member_count']} auto-accepted "
                        f"Delta Learning members woven into one {after.accepted_pv} "
                        "Learning approval."
                    ),
                    "accepted_snapshot_role": "POST_APPROVAL_STORAGE_ONLY",
                    "accepted_archive_opened_for_stamp": False,
                    "accepted_archive_queried_for_stamp": False,
                    "accepted_archive_model_context_source": False,
                    "approval_inferred": False,
                    "stamped_at": utc_now(),
                }
                dual_hil_plan_stamp = {
                    **stamp_body,
                    "receipt_sha256": sha256_bytes(
                        canonical_json_bytes(stamp_body)
                    ),
                }
        elif outcome == HilDecision.ROLLBACK:
            require(
                not correction_delta and not research_question,
                "ROLLBACK_FIELDS_INVALID",
                "ROLLBACK may not include correction or research payloads.",
                status="BLOCKED",
            )
            target_pv, entry_pv, default_used, resolution = (
                self._resolve_rollback_target(session, rollback_to)
            )
            require(
                target_pv in self.store.accepted_ids(project_id),
                "ROLLBACK_TARGET_NOT_ACCEPTED",
                "Rollback may target only an immutable accepted PV in this project.",
                status="BLOCKED",
                target_pv=target_pv,
                accepted=self.store.accepted_ids(project_id),
            )
            target_path = self.store.accepted_path(project_id, target_pv)
            target_freshness = evaluate_freshness(self.store, project_id, target_path)
            result = self.store.rollback(
                project_id,
                target_pv=target_pv,
                expected_pointer_generation=session.metadata[
                    "candidate_pointer_generation"
                ],
                decided_by=decided_by,
                decision_id=exact_decision_id,
                default_entry_target_used=default_used,
                entry_pv=entry_pv,
                candidate_id=candidate_id,
                freshness=target_freshness,
                resolution_reference=resolution,
            )
            after = self.store.pointer(project_id)
            pointer_moved = bool(result["pointer_moved"])
            session.metadata.setdefault("completed_runs", []).append(
                {
                    "run_id": decision_run_id,
                    "task": session.task,
                    "candidate_id": candidate_id,
                    "decision_id": exact_decision_id,
                    "decision": outcome.value,
                    "candidate_preserved_unaccepted": True,
                }
            )
            session.accepted_pv = after.accepted_pv
            session.accepted_pointer_generation = after.generation
            session.candidate_id = None
            session.task = None
            session.metadata.pop("run_id", None)
            session.metadata["source_update_confirmed"] = False
            session.metadata["current_accepted_freshness"] = target_freshness
            session.metadata["source_state"] = (
                "ACCEPTED_ENTRY_EXACT"
                if target_freshness["state"] == "FRESH"
                else "ACCEPTED_ENTRY_STALE_OR_DIFFERENT_LIVE_SOURCE"
            )
            session.metadata["accepted_pv_query_scope"] = (
                "CURRENT_ENTRY_AND_LIVE_SOURCE_EXACT"
                if target_freshness["state"] == "FRESH"
                else "IMMUTABLE_ENTRY_STATE_ONLY_LIVE_SOURCE_DIFFERS"
            )
            session.metadata["last_rollback"] = result["receipt"]
            target_state = (
                SessionState.PVN_ACCEPTED
                if after.accepted_pv == "PV1"
                else SessionState.PVN1_ACCEPTED
            )
            session.state = transition(
                session.state,
                LifecycleEvent.HIL_ROLLBACK,
                target_state,
            )
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
                transition_event = LifecycleEvent.HIL_APPROVE_WITH_DELTA
            elif outcome == HilDecision.MORE_RESEARCH:
                require(
                    bool(research_question and research_question.strip()),
                    "RESEARCH_QUESTION_REQUIRED",
                    "MORE_RESEARCH requires one bounded research question.",
                    status="BLOCKED",
                )
                target_state = SessionState.RESEARCH_TASK_PENDING
                transition_event = LifecycleEvent.HIL_MORE_RESEARCH
            elif outcome == HilDecision.REJECT:
                require(
                    bool(reason and reason.strip()),
                    "REJECTION_REASON_REQUIRED",
                    "REJECT requires an exact reason.",
                    status="BLOCKED",
                )
                target_state = SessionState.REJECTED_RUN
                transition_event = LifecycleEvent.HIL_REJECT
            else:
                require(
                    bool(reason and reason.strip()),
                    "FAILURE_REASON_REQUIRED",
                    "FAIL requires the exact failed gate.",
                    status="BLOCKED",
                )
                target_state = SessionState.FAILED_RUN
                transition_event = LifecycleEvent.HIL_FAIL
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
            session.state = transition(
                session.state,
                transition_event,
                target_state,
            )
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
        backlog_outcome: dict[str, Any] | None = None
        batch_backlog_outcome: dict[str, Any] | None = None
        backlog_task_id = session.metadata.get("active_backlog_task_id")
        if backlog_task_id:
            backlog_outcome = self.store.record_backlog_outcome(
                project_id,
                backlog_task_id=cast(str, backlog_task_id),
                session_id=session_id,
                decision=outcome.value,
                decided_by=decided_by,
                candidate_id=candidate_id,
                accepted_pv=self.store.pointer(project_id).accepted_pv,
                dual_hil_stamp=dual_hil_plan_stamp,
            )
            session.metadata.pop("active_backlog_task_id", None)
            # Refresh keeps the governed Delta ACTIVE while its HIL is pending.
            # Only the explicit human decision records the backlog outcome, so
            # this is the first valid boundary at which the session mirror may
            # become DONE.
            session.metadata["active_backlog_task_status"] = "DONE"
            session.metadata["last_backlog_outcome"] = {
                "task_id": backlog_outcome["task_id"],
                "status": backlog_outcome["status"],
                "decision": outcome.value,
            }
        batch_task_ids = session.metadata.get("batch_backlog_task_ids")
        if batch_task_ids:
            batch_backlog_outcome = self.store.record_backlog_batch_outcome(
                project_id,
                task_ids=cast(list[str], batch_task_ids),
                session_id=session_id,
                decision=outcome.value,
                decided_by=decided_by,
                candidate_id=candidate_id,
                accepted_pv=self.store.pointer(project_id).accepted_pv,
                dual_hil_stamp=dual_hil_plan_stamp,
            )
            session.metadata.pop("batch_backlog_task_ids", None)
            session.metadata["last_batch_backlog_outcome"] = {
                "receipt_id": batch_backlog_outcome["receipt_id"],
                "task_count": batch_backlog_outcome["task_count"],
                "status": batch_backlog_outcome["resulting_status"],
                "decision": outcome.value,
            }
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
            "pointer_moved": pointer_moved,
            "candidate_promoted": outcome == HilDecision.APPROVE,
            "backlog_outcome": backlog_outcome,
            "batch_backlog_outcome": batch_backlog_outcome,
            "dual_hil_plan_stamp": dual_hil_plan_stamp,
        }

    def rollback_state(
        self,
        project_id: str,
        session_id: str,
        *,
        decided_by: str,
        rollback_to: str | None = None,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        """Travel the pointer to accepted history without rewriting source or PVs."""
        session = self.load(project_id, session_id)
        if session.state in {
            SessionState.PV1_CANDIDATE,
            SessionState.PVN1_CANDIDATE,
        }:
            return self.decide(
                project_id,
                session_id,
                decision=HilDecision.ROLLBACK,
                decided_by=decided_by,
                rollback_to=rollback_to,
                decision_id=decision_id,
            )
        require(
            session.state
            in {
                SessionState.BOOTED,
                SessionState.PVN_ACCEPTED,
                SessionState.PVN1_ACCEPTED,
                SessionState.PVN1_ENTRY,
            }
            and session.task is None
            and session.candidate_id is None,
            "ROLLBACK_STATE_INVALID",
            "Pointer state travel is valid only from an idle accepted entry or a "
            "pending candidate HIL.",
            status="BLOCKED",
            state=session.state.value,
        )
        pointer = self.store.pointer(project_id)
        require(
            pointer.accepted_pv is not None,
            "ROLLBACK_NO_ACCEPTED_HISTORY",
            "Rollback requires at least one immutable accepted PV.",
            status="BLOCKED",
        )
        require(
            pointer.generation == session.accepted_pointer_generation,
            "ROLLBACK_SESSION_POINTER_STALE",
            "The accepted pointer changed after this session last synchronized.",
            status="STALE",
            expected_generation=session.accepted_pointer_generation,
            actual_generation=pointer.generation,
        )
        target_pv, entry_pv, default_used, resolution = self._resolve_rollback_target(
            session, rollback_to
        )
        require(
            target_pv in self.store.accepted_ids(project_id),
            "ROLLBACK_TARGET_NOT_ACCEPTED",
            "Rollback may target only an immutable accepted PV in this project.",
            status="BLOCKED",
            target_pv=target_pv,
            accepted=self.store.accepted_ids(project_id),
        )
        target = self.store.accepted_path(project_id, target_pv)
        target_freshness = evaluate_freshness(self.store, project_id, target)
        exact_decision_id = decision_id or prefixed_id("decision")
        result = self.store.rollback(
            project_id,
            target_pv=target_pv,
            expected_pointer_generation=session.accepted_pointer_generation,
            decided_by=decided_by,
            decision_id=exact_decision_id,
            default_entry_target_used=default_used,
            entry_pv=entry_pv,
            candidate_id=None,
            freshness=target_freshness,
            resolution_reference=resolution,
        )
        after = self.store.pointer(project_id)
        session.accepted_pv = after.accepted_pv
        session.accepted_pointer_generation = after.generation
        session.metadata["current_accepted_freshness"] = target_freshness
        session.metadata["last_rollback"] = result["receipt"]
        session.metadata["highest_accepted_ordinal"] = (
            self.store.highest_accepted_ordinal(project_id)
        )
        session.metadata["next_candidate_would_be"] = self.store.next_pv_id(project_id)
        session.metadata["source_state"] = (
            "ACCEPTED_ENTRY_EXACT"
            if target_freshness["state"] == "FRESH"
            else "ACCEPTED_ENTRY_STALE_OR_DIFFERENT_LIVE_SOURCE"
        )
        session.metadata["accepted_pv_query_scope"] = (
            "CURRENT_ENTRY_AND_LIVE_SOURCE_EXACT"
            if target_freshness["state"] == "FRESH"
            else "IMMUTABLE_ENTRY_STATE_ONLY_LIVE_SOURCE_DIFFERS"
        )
        target_state = (
            SessionState.PVN_ACCEPTED
            if after.accepted_pv == "PV1"
            else SessionState.PVN1_ACCEPTED
        )
        session.state = transition(
            session.state,
            LifecycleEvent.HIL_ROLLBACK,
            target_state,
        )
        session.metadata.setdefault("decisions", []).append(result["receipt"])
        self._save(session)
        ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="hil.rollback",
            visible_payload=result["receipt"],
            occurred_at=utc_now(),
            session_id=session_id,
        )
        return {
            "status": "PASS",
            "decision": result["receipt"],
            "session": session.as_dict(),
            "pointer": after.as_dict(),
            "pointer_advanced": False,
            "pointer_moved": result["pointer_moved"],
            "candidate_promoted": False,
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
        self._verify_repository_matches_identity(
            project_id,
            self.store.accepted_metadata(project_id, cast(str, pointer.accepted_pv))[
                "project_identity"
            ],
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
        target_state = (
            SessionState.PVN_ACCEPTED
            if pointer.accepted_pv == "PV1"
            else SessionState.PVN1_ACCEPTED
        )
        session.state = transition(
            session.state,
            LifecycleEvent.RETURN_TO_ACCEPTED,
            target_state,
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

    def reopen_unpresented_candidate_for_delta_exit(
        self,
        project_id: str,
        session_id: str,
        *,
        task_id: str,
        candidate_id: str,
        confirmation: str,
        reason: str,
    ) -> dict[str, Any]:
        """Preserve one premature proposal and restore its ACTIVE Delta."""

        session = self.load(project_id, session_id)
        exact_task_id = str(task_id or "").strip()
        exact_candidate_id = str(candidate_id or "").strip()
        exact_reason = str(reason or "").strip()
        require(
            confirmation == "REOPEN_UNPRESENTED_CANDIDATE_FOR_DELTA_EXIT"
            and bool(exact_task_id)
            and bool(exact_candidate_id)
            and bool(exact_reason),
            "PREMATURE_CANDIDATE_CORRECTION_CONFIRMATION_INVALID",
            "Premature-candidate correction requires its exact confirmation, task, candidate, and reason.",
            status="BLOCKED",
        )
        task = cast(dict[str, Any], session.task or {})
        require(
            session.state in {SessionState.PV1_CANDIDATE, SessionState.PVN1_CANDIDATE}
            and session.candidate_id == exact_candidate_id
            and bool(str(task.get("task_id") or "").strip())
            and session.metadata.get("active_backlog_task_id") == exact_task_id
            and session.metadata.get("active_backlog_task_status") == "ACTIVE"
            and not session.metadata.get("pending_hil")
            and not isinstance(session.metadata.get("pending_task"), dict),
            "PREMATURE_CANDIDATE_CORRECTION_STATE_MISMATCH",
            "Only the exact unpresented candidate owned by the sole ACTIVE Delta may be reopened.",
            status="MISMATCH",
            state=session.state.value,
            candidate_id=session.candidate_id,
        )
        pointer = self.store.pointer(project_id)
        require(
            pointer.accepted_pv == session.accepted_pv
            and pointer.generation == session.accepted_pointer_generation,
            "PREMATURE_CANDIDATE_CORRECTION_POINTER_MISMATCH",
            "The accepted pointer changed after the premature proposal was sealed.",
            status="MISMATCH",
        )
        proposal_path = self.store._candidate_overlay_receipt_path(
            project_id, exact_candidate_id
        )
        require(
            proposal_path.is_file(),
            "PREMATURE_CANDIDATE_CORRECTION_RECEIPT_MISSING",
            "The premature live-root proposal receipt is unavailable.",
            status="MISMATCH",
        )
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
        proposal_body = dict(proposal)
        proposal_sha256 = str(proposal_body.pop("receipt_sha256", ""))
        require(
            proposal.get("project_id") == project_id
            and proposal.get("candidate_id") == exact_candidate_id
            and proposal_sha256 == sha256_bytes(canonical_json_bytes(proposal_body)),
            "PREMATURE_CANDIDATE_CORRECTION_RECEIPT_MISMATCH",
            "The premature proposal receipt failed its immutable identity check.",
            status="MISMATCH",
        )
        receipt_body = {
            "schema": "evidence-lane.premature-candidate-delta-exit-correction.v1",
            "status": "PASS",
            "project_id": project_id,
            "session_id": session_id,
            "task_id": exact_task_id,
            "candidate_id": exact_candidate_id,
            "proposal_receipt_sha256": proposal_sha256,
            "reason": exact_reason,
            "from_state": session.state.value,
            "to_state": SessionState.TASK_CLASSIFIED.value,
            "accepted_pv": pointer.accepted_pv,
            "pointer_generation": pointer.generation,
            "candidate_history_preserved": True,
            "candidate_accepted": False,
            "hil_decision_recorded": False,
            "pointer_moved": False,
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
            "corrected_at": utc_now(),
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        receipt_path = (
            self.store.project_root(project_id)
            / "receipts"
            / "candidate-corrections"
            / f"{exact_candidate_id}__delta-exit.json"
        )
        if receipt_path.is_file():
            existing = json.loads(receipt_path.read_text(encoding="utf-8"))
            require(
                existing == receipt,
                "PREMATURE_CANDIDATE_CORRECTION_REPLAY_CONFLICT",
                "The premature-candidate correction receipt already differs.",
                status="MISMATCH",
            )
        else:
            atomic_write_json(receipt_path, receipt)
        session.metadata.setdefault("premature_candidate_corrections", []).append(
            receipt
        )
        session.candidate_id = None
        session.state = transition(
            session.state,
            LifecycleEvent.REOPEN_UNPRESENTED_CANDIDATE_FOR_DELTA_EXIT,
            SessionState.TASK_CLASSIFIED,
        )
        self._save(session)
        receipt_sha256 = str(receipt["receipt_sha256"])
        event = ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="pv.candidate.reopened_for_delta_exit",
            visible_payload=receipt,
            occurred_at=cast(str, receipt["corrected_at"]),
            session_id=session_id,
            task_id=exact_task_id,
            run_id=cast(str, session.metadata["run_id"]),
            event_id=f"candidate-reopen-{receipt_sha256[:24].lower()}",
        )
        return {
            "status": "PASS",
            "receipt": receipt,
            "receipt_path": str(receipt_path),
            "event": event,
            "session": session.as_dict(),
            "pointer": pointer.as_dict(),
        }

    def _state_travel_target(self, session: SessionRecord) -> tuple[str, str]:
        if session.host in {
            HostKind.CODEX_DESKTOP,
            HostKind.CODEX_CLI,
            HostKind.CODEX_VM,
        }:
            return "NEW_CODEX_TASK", "OPEN_NEW_CODEX_TASK"
        return "NEW_HOST_SESSION", "OPEN_NEW_HOST_SESSION"

    @staticmethod
    def _state_travel_canonical_task_rows(
        backlog: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Project the current executable Goal without falsifying Plan history.

        Canonical history remains sealed separately by the Plan snapshot. A
        SUPERSEDED or DROPPED row is immutable history, not completed work, and
        therefore must never appear in the destination's executable task panel.
        """

        rows: list[dict[str, Any]] = []
        for row in cast(
            list[dict[str, Any]],
            backlog["goal_projection"]["rows"],
        ):
            projected = {
                "number": int(row["number"]),
                "task_id": str(row["task_id"]),
                "step": str(row["step"]),
                "status": str(row["status"]).upper(),
                "canonical_plan_sequence": int(row["plan_sequence"]),
                "steer_deltas": list(row.get("steer_deltas") or []),
            }
            for metadata_field in (
                "task_classification",
                "plan_group",
                "commit_batch_id",
                "dependencies",
                "dependency_source",
                "git_commit_stage",
                "git_commit_stage_source",
                "visible_label",
            ):
                if metadata_field in row:
                    projected[metadata_field] = row[metadata_field]
            if row.get("panel_role"):
                projected["panel_role"] = str(row["panel_role"])
            rows.append(projected)
        return rows

    def _state_travel_plan_snapshot(self, project_id: str) -> dict[str, Any]:
        backlog = self.store.backlog_status(project_id)
        goal = cast(dict[str, Any], backlog["goal_projection"])
        history = cast(dict[str, Any], backlog["history_projection"])
        canonical = cast(dict[str, Any], backlog["canonical_plan_projection"])
        body = {
            "canonical_authority": "PLAN_LANE",
            "task_count": canonical["task_count"],
            "canonical_plan_sha256": canonical["projection_sha256"],
            "executable_task_count": goal["task_count"],
            "goal_projection_sha256": goal["projection_sha256"],
            "goal_row_offset": goal.get("row_offset", 0),
            "goal_row_start": goal.get("row_start"),
            "goal_row_end": goal.get("row_end"),
            "history_task_count": history["task_count"],
            "history_projection_sha256": history["projection_sha256"],
            "event_count": backlog["event_count"],
            "event_head_sha256": backlog["event_head_sha256"],
            "planning_mode_event_count": backlog["planning_mode_event_count"],
            "planning_mode_event_head_sha256": backlog[
                "planning_mode_event_head_sha256"
            ],
            "active_task_ids": [str(row["task_id"]) for row in backlog["active"]],
        }
        return {
            **body,
            "snapshot_sha256": sha256_bytes(canonical_json_bytes(body)),
        }

    def _state_travel_candidate_snapshot(
        self,
        project_id: str,
        candidate_id: str | None,
    ) -> dict[str, Any] | None:
        if not candidate_id:
            return None
        validation = self.store.candidate_validation(
            project_id,
            candidate_id,
            require_promotable=False,
        )
        return {
            "candidate_id": candidate_id,
            "proposed_pv": validation["proposed_pv"],
            "manifest_sha256": validation["manifest_sha256"],
            "package_sha256": validation["package_sha256"],
            "promotable": validation["promotable"],
        }

    def _state_travel_source_snapshot(self, project_id: str) -> dict[str, Any]:
        config = self.store.config(project_id)
        identity = inspect_repository(
            config.repository_path,
            expected_owner=config.expected_owner,
            expected_name=config.expected_name,
        )
        body = identity_json(identity, config.repository_path)
        return {
            **body,
            "identity_sha256": sha256_bytes(canonical_json_bytes(body)),
        }

    def _direct_state_travel_source_identity(
        self,
        project_id: str,
    ) -> dict[str, Any]:
        """Seal the live dirty path set and bytes, not only HEAD and tree."""

        config = self.store.config(project_id)
        repository = Path(config.repository_path).resolve()
        base = self._state_travel_source_snapshot(project_id)
        status = run_git(
            repository,
            ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        ).stdout
        tracked_diff_sha256, _tracked_diff_bytes = run_git_digest(
            repository,
            ["diff", "--binary", "--no-ext-diff", "--full-index", "HEAD", "--", "."],
        )
        tracked_paths = [
            value.replace("\\", "/")
            for value in run_git(
                repository,
                ["diff", "--name-only", "-z", "HEAD", "--", "."],
            ).stdout.split("\0")
            if value
        ]
        untracked_paths = [
            value.replace("\\", "/")
            for value in run_git(
                repository,
                ["ls-files", "--others", "--exclude-standard", "-z"],
            ).stdout.split("\0")
            if value
        ]
        dirty_paths = sorted(set(tracked_paths + untracked_paths))
        members: list[dict[str, Any]] = []
        for relative in dirty_paths:
            target = (repository / Path(relative)).resolve()
            try:
                target.relative_to(repository)
            except ValueError as exc:
                raise EvidenceLaneError(
                    "DIRECT_STATE_TRAVEL_DIRTY_PATH_ESCAPE",
                    "A dirty Git path escaped the exact same worktree.",
                    status="MISMATCH",
                    details={"path": relative},
                ) from exc
            if target.is_symlink():
                link_bytes = str(target.readlink()).encode("utf-8")
                members.append(
                    {
                        "path": relative,
                        "state": "SYMLINK",
                        "size": len(link_bytes),
                        "sha256": sha256_bytes(link_bytes),
                    }
                )
            elif target.is_file():
                members.append(
                    {
                        "path": relative,
                        "state": "FILE",
                        "size": target.stat().st_size,
                        "sha256": sha256_file(target),
                    }
                )
            else:
                members.append(
                    {
                        "path": relative,
                        "state": "DELETED",
                        "size": None,
                        "sha256": None,
                    }
                )
        body = {
            "repository_path": str(repository),
            "repository_url": base.get("repository_url"),
            "owner": base.get("owner"),
            "name": base.get("name"),
            "branch": base.get("branch"),
            "commit_sha": base.get("commit_sha"),
            "tree_sha": base.get("tree_sha"),
            "worktree_sha256": base.get("worktree_sha256"),
            "is_clean": base.get("is_clean"),
            "status_record_count": len(
                [value for value in status.split("\0") if value]
            ),
            "status_sha256": sha256_bytes(status.encode("utf-8")),
            "tracked_diff_sha256": tracked_diff_sha256,
            "dirty_path_count": len(dirty_paths),
            "dirty_path_set_sha256": sha256_bytes(canonical_json_bytes(dirty_paths)),
            "dirty_content_sha256": sha256_bytes(canonical_json_bytes(members)),
        }
        return {
            **body,
            "identity_sha256": sha256_bytes(canonical_json_bytes(body)),
        }

    def _direct_state_travel_plan_identity(
        self,
        project_id: str,
    ) -> dict[str, Any]:
        """Derive ACTIVE/batch/window/HIL anchors from the current Plan SQLite."""

        backlog = self.store.backlog_status(project_id)
        goal = cast(dict[str, Any], backlog["goal_projection"])
        history = cast(dict[str, Any], backlog["history_projection"])
        canonical = cast(dict[str, Any], backlog["canonical_plan_projection"])
        rows = [dict(row) for row in cast(list[dict[str, Any]], goal["rows"])]
        active = [row for row in rows if row.get("status") == "in_progress"]
        require(
            len(active) == 1 and bool(rows),
            "DIRECT_STATE_TRAVEL_SOLE_ACTIVE_PLAN_ROW_REQUIRED",
            "Direct same-worktree entry requires exactly one live ACTIVE Plan row.",
            status="MISMATCH",
            active_count=len(active),
        )
        active_row = active[0]
        active_index = rows.index(active_row)
        next_hils = [
            row
            for row in rows[active_index + 1 :]
            if row.get("panel_role") in {"HIL_GATE", "PHYSICALLY_FINAL_HIL"}
            and row.get("status") == "pending"
        ]
        final_hils = [
            row for row in rows if row.get("panel_role") == "PHYSICALLY_FINAL_HIL"
        ]
        require(
            bool(next_hils) and len(final_hils) == 1 and final_hils[0] == rows[-1],
            "DIRECT_STATE_TRAVEL_HIL_ANCHORS_INVALID",
            "The live Plan must derive one next HIL and one physically final HIL.",
            status="MISMATCH",
        )
        source_batch_id = str(active_row.get("commit_batch_id") or "").strip()
        require(
            bool(source_batch_id),
            "DIRECT_STATE_TRAVEL_ACTIVE_BATCH_REQUIRED",
            "The live ACTIVE Plan row requires its canonical commit batch.",
            status="MISMATCH",
        )
        fixed_window_task_ids = self.store.persisted_host_plan_window_task_ids(
            project_id
        )
        require(
            bool(fixed_window_task_ids),
            "DIRECT_STATE_TRAVEL_FIXED_HOST_BATCH_REQUIRED",
            "Direct same-worktree entry requires the persisted canonical host Plan batch.",
            status="MISMATCH",
        )
        fixed_window_task_ids = cast(list[str], fixed_window_task_ids)
        row_by_task_id = {str(row["task_id"]): row for row in rows}
        require(
            all(task_id in row_by_task_id for task_id in fixed_window_task_ids),
            "DIRECT_STATE_TRAVEL_FIXED_HOST_BATCH_TASK_MISSING",
            "A persisted host Plan batch task is absent from live canonical authority.",
            status="MISMATCH",
        )
        window_rows = [row_by_task_id[task_id] for task_id in fixed_window_task_ids]
        window_indexes = [rows.index(row) for row in window_rows]
        require(
            window_indexes
            == list(range(window_indexes[0], window_indexes[0] + len(window_indexes)))
            and active_row in window_rows,
            "DIRECT_STATE_TRAVEL_FIXED_HOST_BATCH_INVALID",
            "The persisted host Plan batch must be contiguous and contain the sole ACTIVE row.",
            status="MISMATCH",
        )
        batch_start = int(window_rows[0]["number"])
        batch_end = int(window_rows[-1]["number"])
        active_batch_id = f"FIXED_HOST_BATCH_R{batch_start}-R{batch_end}"
        snapshot = self._state_travel_plan_snapshot(project_id)
        body = {
            "canonical_plan_sha256": canonical["projection_sha256"],
            "goal_projection_sha256": goal["projection_sha256"],
            "history_projection_sha256": history["projection_sha256"],
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "row_start": int(goal["row_start"]),
            "row_end": int(goal["row_end"]),
            "task_count": int(goal["task_count"]),
            "active_row": int(active_row["number"]),
            "active_task_id": str(active_row["task_id"]),
            "active_batch_id": active_batch_id,
            "active_row_commit_batch_id": source_batch_id,
            "active_batch_row_start": batch_start,
            "active_batch_row_end": batch_end,
            "host_window_row_start": batch_start,
            "host_window_row_end": batch_end,
            "next_hil_row": int(next_hils[0]["number"]),
            "next_hil_task_id": str(next_hils[0]["task_id"]),
            "physically_final_hil_row": int(final_hils[0]["number"]),
            "physically_final_hil_task_id": str(final_hils[0]["task_id"]),
        }
        return {
            **body,
            "identity_sha256": sha256_bytes(canonical_json_bytes(body)),
        }

    @staticmethod
    def _state_travel_task_deep_link(
        host_kind: str,
        task_id: str,
    ) -> str | None:
        if not host_kind.startswith("CODEX") or not task_id:
            return None
        return f"codex://threads/{task_id}"

    @staticmethod
    def _state_travel_destination_resolution(
        creation: dict[str, Any],
        *,
        destination_task_id: str,
        destination_task_deep_link: str | None,
    ) -> dict[str, Any]:
        """Resolve one queued clientThreadId to one live destination identity."""

        raw_client_thread_id = creation.get(
            "client_thread_id",
            creation.get("clientThreadId"),
        )
        client_thread_id = str(raw_client_thread_id or "").strip()
        resolution = creation.get("destination_resolution")
        if not client_thread_id:
            require(
                creation.get("destination_task_id") == destination_task_id
                and creation.get("destination_task_deep_link")
                == destination_task_deep_link,
                "STATE_TRAVEL_DESTINATION_CREATION_BINDING_MISMATCH",
                "The host destination-creation receipt does not bind the exact "
                "destination task UUID and deep link.",
                status="MISMATCH",
            )
            return {
                "schema": "evidence-lane.host-destination-resolution.v1",
                "status": "DIRECT_DESTINATION_TASK_ID",
                "client_thread_id": None,
                "destination_task_id": destination_task_id,
                "destination_task_deep_link": destination_task_deep_link,
                "live_destination_task_ids": [destination_task_id],
                "duplicate_task_ids": [],
                "archived_task_ids": [],
            }

        require(
            isinstance(resolution, dict),
            "STATE_TRAVEL_DESTINATION_CLIENT_THREAD_UNRESOLVED",
            "A queued clientThreadId must resolve to exactly one real destination "
            "task before State Travel resume.",
            status="BLOCKED",
            client_thread_id=client_thread_id,
        )
        resolution = cast(dict[str, Any], resolution)

        def task_id_list(field: str) -> list[str]:
            raw = resolution.get(field, [])
            require(
                isinstance(raw, list)
                and len(raw) <= 100
                and all(
                    isinstance(value, str)
                    and bool(value.strip())
                    and len(value.strip()) <= 256
                    for value in raw
                ),
                "STATE_TRAVEL_DESTINATION_RESOLUTION_HISTORY_INVALID",
                "Destination resolution history must contain bounded task IDs.",
                status="BLOCKED",
                field=field,
            )
            normalized = [value.strip() for value in cast(list[str], raw)]
            require(
                len(normalized) == len(set(normalized)),
                "STATE_TRAVEL_DESTINATION_RESOLUTION_HISTORY_DUPLICATE",
                "Destination resolution history cannot repeat one task identity.",
                status="MISMATCH",
                field=field,
            )
            return normalized

        live_task_ids = task_id_list("live_destination_task_ids")
        duplicate_task_ids = task_id_list("duplicate_task_ids")
        archived_task_ids = task_id_list("archived_task_ids")
        require(
            resolution.get("schema") == "evidence-lane.host-destination-resolution.v1"
            and resolution.get("status") == "RESOLVED_UNIQUE"
            and str(resolution.get("client_thread_id") or "").strip()
            == client_thread_id
            and resolution.get("destination_task_id") == destination_task_id
            and resolution.get("destination_task_deep_link")
            == destination_task_deep_link
            and live_task_ids == [destination_task_id]
            and destination_task_id not in duplicate_task_ids
            and destination_task_id not in archived_task_ids
            and not set(duplicate_task_ids).intersection(archived_task_ids),
            "STATE_TRAVEL_DESTINATION_CLIENT_THREAD_AMBIGUOUS",
            "The queued clientThreadId did not resolve to exactly one live "
            "destination; duplicate and archived identities remain history only.",
            status="MISMATCH",
            client_thread_id=client_thread_id,
            live_destination_task_ids=live_task_ids,
            duplicate_task_ids=duplicate_task_ids,
            archived_task_ids=archived_task_ids,
        )
        return {
            "schema": "evidence-lane.host-destination-resolution.v1",
            "status": "RESOLVED_UNIQUE",
            "client_thread_id": client_thread_id,
            "destination_task_id": destination_task_id,
            "destination_task_deep_link": destination_task_deep_link,
            "live_destination_task_ids": live_task_ids,
            "duplicate_task_ids": duplicate_task_ids,
            "archived_task_ids": archived_task_ids,
        }

    @staticmethod
    def _state_travel_plugin_build_identity() -> dict[str, Any]:
        """Seal the package-local build rather than trusting a slot title."""

        manifest_path = (
            resolve_plugin_root(__file__) / ".codex-plugin" / "plugin.json"
        )
        require(
            manifest_path.is_file(),
            "STATE_TRAVEL_PLUGIN_MANIFEST_MISSING",
            "State Travel requires the running package-local plugin manifest.",
            status="BLOCKED",
        )
        manifest_bytes = manifest_path.read_bytes()
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceLaneError(
                "STATE_TRAVEL_PLUGIN_MANIFEST_INVALID",
                "The running package-local plugin manifest is not valid UTF-8 JSON.",
                status="BLOCKED",
            ) from exc
        plugin_name = str(manifest.get("name") or "").strip()
        plugin_version = str(manifest.get("version") or "").strip()
        require(
            plugin_name == "evidence-lane-plugin"
            and bool(plugin_version)
            and plugin_version.split("+", 1)[0] == ENGINE_VERSION,
            "STATE_TRAVEL_PLUGIN_BUILD_IDENTITY_MISMATCH",
            "The running plugin manifest does not match the Evidence Lane engine.",
            status="MISMATCH",
            plugin_name=plugin_name or None,
            plugin_version=plugin_version or None,
            engine_version=ENGINE_VERSION,
        )
        plugin_root = resolve_plugin_root(__file__)
        routing_path = (
            plugin_root / "skills" / "evi" / "references" / "mcp-tool-routing.v1.json"
        )
        require(
            routing_path.is_file(),
            "STATE_TRAVEL_ROUTING_MANIFEST_MISSING",
            "State Travel requires the running package's exact MCP routing catalog.",
            status="BLOCKED",
        )
        body = {
            "schema": "evidence-lane.state-travel-plugin-build.v1",
            "plugin_name": plugin_name,
            "plugin_version": plugin_version,
            "engine_version": ENGINE_VERSION,
            "plugin_manifest_sha256": sha256_bytes(manifest_bytes),
            "routing_manifest_sha256": sha256_file(routing_path),
            "tool_count": NATIVE_TOOL_COUNT,
            "read_tool_count": NATIVE_READ_TOOL_COUNT,
            "write_tool_count": NATIVE_WRITE_TOOL_COUNT,
            "governed_skill_count": GOVERNED_SKILL_COUNT,
        }
        return {
            **body,
            "identity_sha256": sha256_bytes(canonical_json_bytes(body)),
        }

    @staticmethod
    def _direct_entry_task_binding_authority(
        session: SessionRecord,
        *,
        project_id: str,
        destination: dict[str, Any],
        exact_binding: dict[str, Any],
        plan: dict[str, Any],
        runtime_instance_attestation: dict[str, Any],
        rebound_at: str,
    ) -> dict[str, Any]:
        """Bind the exact task contract to the server-attested destination."""

        destination_task_id = str(destination.get("task_id") or "").strip()
        active_plan_task_id = str(plan.get("active_task_id") or "").strip()
        runtime_task = cast(dict[str, Any], session.task or {})
        runtime_task_id = str(runtime_task.get("task_id") or "").strip()
        require(
            bool(destination_task_id)
            and bool(active_plan_task_id)
            and bool(runtime_task_id)
            and session.metadata.get("current_host_session_id") == destination_task_id
            and session.metadata.get("active_backlog_task_id") == active_plan_task_id
            and session.metadata.get("active_backlog_task_status") == "ACTIVE"
            and runtime_instance_attestation.get("status") == "PASS"
            and runtime_instance_attestation.get("caller_supplied") is False
            and runtime_instance_attestation.get("process_id_exposed") is False
            and len(str(runtime_instance_attestation.get("receipt_sha256") or ""))
            == 64,
            "DIRECT_STATE_TRAVEL_TASK_BINDING_AUTHORITY_MISMATCH",
            "Direct entry cannot bind the task contract without the exact destination, active Plan row, runtime task, and server attestation.",
            status="MISMATCH",
            writes_performed=False,
        )
        history = session.metadata.setdefault("active_contract_rebinds", [])
        require(
            isinstance(history, list),
            "DIRECT_STATE_TRAVEL_TASK_BINDING_HISTORY_INVALID",
            "The session task-binding history must remain append-only data.",
            status="MISMATCH",
            writes_performed=False,
        )
        binding_sha256 = sha256_bytes(canonical_json_bytes(exact_binding))
        request_nonce_sha256 = sha256_bytes(
            str(exact_binding.get("request_nonce") or "").encode("utf-8")
        )
        prior = session.metadata.get("active_contract_rebind_receipt")
        prior_receipt_sha256 = (
            str(cast(dict[str, Any], prior).get("receipt_sha256") or "")
            if isinstance(prior, dict)
            else None
        )
        rebind_id = f"direct_rebind_{binding_sha256[:40].lower()}"
        task_contract_sha256 = sha256_bytes(canonical_json_bytes(runtime_task))
        rebind_body = {
            "schema": "evidence-lane.active-contract-session-rebind.v1",
            "status": "PASS",
            "project_id": project_id,
            "session_id": session.session_id,
            "rebind_id": rebind_id,
            "request_sha256": binding_sha256,
            "approval_receipt_sha256": None,
            "active_plan_task_id": active_plan_task_id,
            "runtime_task_id": runtime_task_id,
            "host_task_id": destination_task_id,
            "prior_runtime_contract_sha256": task_contract_sha256,
            "replacement_runtime_contract_sha256": task_contract_sha256,
            "runtime_task_identity_preserved": True,
            "active_plan_row_identity_preserved": True,
            "governed_session_identity_preserved": True,
            "host_task_identity_preserved": True,
            "authority_route": "DIRECT_FORCED_SAME_WORKTREE_NEW_TASK",
            "direct_entry_authority": {
                "normalized_binding_sha256": binding_sha256,
                "request_nonce_sha256": request_nonce_sha256,
                "destination_task_uri_sha256": sha256_bytes(
                    f"codex://threads/{destination_task_id}".encode()
                ),
                "runtime_instance_attestation_receipt_sha256": (
                    runtime_instance_attestation["receipt_sha256"]
                ),
                "runtime_instance_attestation_mode": "SERVER_DERIVED_ATTESTATION",
                "caller_supplied_runtime_identity": False,
            },
            "task_binding_contract": {
                "manager_scope": "SHARED_MULTI_PROJECT_MULTI_TASK",
                "registry_mutability": "MUTABLE_APPEND_OR_REFRESH",
                "invocation_binding_scope": "EXACT_CALLING_TASK",
                "reentry_target": destination_task_id,
                "installer_helper": "SEPARATE_COMPONENT",
            },
            "prior_active_contract_rebind_receipt_sha256": (
                prior_receipt_sha256 or None
            ),
            "candidate_created": False,
            "candidate_id_preserved": session.candidate_id,
            "candidate_state_preserved": session.state.value,
            "pending_hil": bool(session.metadata.get("pending_hil")),
            "pending_task_sha256": (
                sha256_bytes(
                    canonical_json_bytes(
                        cast(dict[str, Any], session.metadata["pending_task"])
                    )
                )
                if isinstance(session.metadata.get("pending_task"), dict)
                else None
            ),
            "pointer_moved": False,
            "goal_completion_mutated": False,
            "git_executed": False,
            "install_executed": False,
            "helper_launched": False,
            "tunnel_launched": False,
            "rebound_at": rebound_at,
        }
        receipt = {
            **rebind_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(rebind_body)),
        }
        existing = next(
            (
                item
                for item in cast(list[Any], history)
                if isinstance(item, dict) and item.get("rebind_id") == rebind_id
            ),
            None,
        )
        require(
            existing is None or existing == receipt,
            "DIRECT_STATE_TRAVEL_TASK_BINDING_AUTHORITY_CONFLICT",
            "The direct-entry task-binding identity already contains different sealed bytes.",
            status="MISMATCH",
            writes_performed=False,
        )
        if existing is None:
            cast(list[dict[str, Any]], history).append(receipt)
        session.metadata["active_contract_rebind_receipt"] = receipt
        return receipt

    def _server_derived_direct_same_worktree_binding(
        self,
        project_id: str,
        session_id: str,
        *,
        authoritative_source_task_id: str,
        runtime_attachment_donor_task_id: str,
        destination_task_id: str,
        destination_task_title: str,
    ) -> dict[str, Any]:
        """Derive the complete direct-entry binding inside the server lock.

        The public route supplies only the three Codex task identities and the
        already-visible destination title. Every volatile source, Plan,
        pointer, package, runtime, profile, and replay field is derived from
        current durable authority by the serving runtime.
        """

        session = self.load(project_id, session_id)
        source_task_id = str(authoritative_source_task_id or "").strip().lower()
        donor_task_id = str(runtime_attachment_donor_task_id or "").strip().lower()
        exact_destination_task_id = str(destination_task_id or "").strip().lower()
        exact_destination_title = str(destination_task_title or "").strip()
        current_host_session_id = (
            str(session.metadata.get("current_host_session_id") or "").strip().lower()
        )
        host_history_ids = {
            str(row.get("host_session_id") or "").strip().lower()
            for row in session.metadata.get("host_session_history", [])
            if isinstance(row, dict)
        }
        require(
            len({source_task_id, donor_task_id, exact_destination_task_id}) == 3,
            "DIRECT_STATE_TRAVEL_TASK_ROLE_COLLISION",
            "Source authority, runtime donor, and destination must be distinct tasks.",
            status="MISMATCH",
            writes_performed=False,
        )
        require(
            current_host_session_id == exact_destination_task_id,
            "DIRECT_STATE_TRAVEL_DESTINATION_BOOT_REQUIRED",
            "The exact destination must complete native verification and attach the existing governed session through session_resume before forced State Travel.",
            status="BLOCKED",
            current_host_session_id=current_host_session_id or None,
            destination_task_id=exact_destination_task_id or None,
            required_current_route="session_resume",
            session_boot_allowed=False,
            direct_route_retry_allowed=False,
            writes_performed=False,
        )
        require(
            source_task_id in host_history_ids and donor_task_id in host_history_ids,
            "DIRECT_STATE_TRAVEL_HOST_HISTORY_MISMATCH",
            "The authoritative source and runtime donor are not both present in this governed session history.",
            status="MISMATCH",
            writes_performed=False,
        )
        existing = session.metadata.get("direct_forced_same_worktree_entry")
        if isinstance(existing, dict) and existing.get("status") == "PASS":
            existing_destination = cast(
                dict[str, Any],
                cast(dict[str, Any], existing.get("host_task_binding") or {}).get(
                    "destination"
                )
                or {},
            )
            require(
                existing_destination.get("task_id") != exact_destination_task_id,
                "DIRECT_STATE_TRAVEL_REPLAY_FORBIDDEN",
                "The destination already owns a committed direct State Travel receipt.",
                status="BLOCKED",
                destination_task_id=exact_destination_task_id,
                writes_performed=False,
            )

        config = self.store.config(project_id)
        pointer = self.store.pointer(project_id)
        source = self._direct_state_travel_source_identity(project_id)
        plan = self._direct_state_travel_plan_identity(project_id)
        plugin = self._state_travel_plugin_build_identity()
        profile_value = session.metadata.get("execution_profile")
        require(
            isinstance(profile_value, dict),
            "DIRECT_STATE_TRAVEL_EXECUTION_PROFILE_REQUIRED",
            "The governed session must retain its exact execution profile.",
            status="MISMATCH",
            writes_performed=False,
        )
        execution_profile = cast(dict[str, Any], profile_value)
        pointer_sha256 = sha256_bytes(canonical_json_bytes(pointer.as_dict()))
        internal_nonce = prefixed_id("server_direct_state_travel")
        return {
            "schema": "evidence-lane.direct-forced-same-worktree-entry.v1",
            "route": "DIRECT_FORCED_SAME_WORKTREE_NEW_TASK",
            "confirmation": "DIRECT_FORCE_SAME_WORKTREE_STATE_TRAVEL",
            "request_nonce": internal_nonce,
            "authoritative_source": {
                "task_id": source_task_id,
                "deep_link": f"codex://threads/{source_task_id}",
            },
            "runtime_attachment_donor": {
                "task_id": donor_task_id,
                "deep_link": f"codex://threads/{donor_task_id}",
            },
            "destination": {
                "task_id": exact_destination_task_id,
                "deep_link": f"codex://threads/{exact_destination_task_id}",
                "title": exact_destination_title,
                "project_id": project_id,
                "workspace_path": config.repository_path,
                "creation_kind": "FRESH_NATIVE_CODEX_LOCAL_PROJECT_TASK",
                "fresh_local_task": True,
                "fork": False,
                "continued_from_chat": False,
            },
            "sole_writer": {
                "policy": "SOLE_WRITER",
                "writer_id": exact_destination_task_id,
                "concurrent_writer_count": 1,
            },
            "sealed_transport": {
                "prepare_called": False,
                "resume_called": False,
                "transport_envelope_created": False,
                "transport_envelope_consumed": False,
                "eligible_fresh_handoff_exists": False,
            },
            "host_context": {
                "current_task_id": exact_destination_task_id,
                "current_task_deep_link": (
                    f"codex://threads/{exact_destination_task_id}"
                ),
                "current_task_title": exact_destination_title,
                "runtime_instance_attestation_mode": "SERVER_DERIVED_ATTESTATION",
                "thread_hydration_mode": "BOUNDED_AUTHORITY_AND_PLAN_SQLITE_ONLY",
                "full_thread_history_requested": False,
                "task7_chat_history_loaded_as_authority": False,
                "collaboration_overlay_active": False,
            },
            "expected": {
                "pointer": {
                    "accepted_pv": pointer.accepted_pv,
                    "generation": pointer.generation,
                    "pointer_sha256": pointer_sha256,
                },
                "source": source,
                "prebootstrap_source": {
                    **source,
                    "captured_before_authorized_route_bootstrap": True,
                },
                "plan": plan,
                "plugin": plugin,
                "runtime": {
                    "state": session.state.value,
                    "generation": pointer.generation,
                    "attachment_donor_task_id": donor_task_id,
                    "runtime_instance_attestation_mode": ("SERVER_DERIVED_ATTESTATION"),
                    "hooks_mode": "OFF_UNTIL_REPAIRED",
                },
                "execution_profile": execution_profile,
            },
        }

    def direct_force_same_worktree_entry(
        self,
        project_id: str,
        session_id: str,
        *,
        binding: dict[str, Any],
        persistence_mode: str,
        persistence_route: dict[str, Any],
        flash: dict[str, Any],
        client_can_edit_source: bool | None,
        server_has_durable_filesystem: bool | None,
    ) -> dict[str, Any]:
        """Verify and bind one fresh native task without a sealed handoff."""

        exact = normalize_direct_forced_same_worktree_binding(binding)
        nonce = str(exact["request_nonce"])
        journal_path = (
            self.store.project_root(project_id)
            / "direct_state_travel_entries"
            / f"direct_{sha256_bytes(nonce.encode('utf-8'))[:32].lower()}.json"
        )
        require(
            not journal_path.exists(),
            "DIRECT_STATE_TRAVEL_REPLAY_FORBIDDEN",
            "The direct/forced same-worktree entry route is single-use and cannot be replayed.",
            status="BLOCKED",
            request_nonce=nonce,
            writes_performed=False,
        )
        session = self.load(project_id, session_id)
        pointer = self.store.pointer(project_id)
        config = self.store.config(project_id)
        source = self._direct_state_travel_source_identity(project_id)
        plan = self._direct_state_travel_plan_identity(project_id)
        plugin = self._state_travel_plugin_build_identity()
        expected = cast(dict[str, Any], exact["expected"])
        expected_pointer = cast(dict[str, Any], expected["pointer"])
        expected_source = cast(dict[str, Any], expected["source"])
        prebootstrap = cast(dict[str, Any], expected["prebootstrap_source"])
        expected_plan = cast(dict[str, Any], expected["plan"])
        expected_plugin = cast(dict[str, Any], expected["plugin"])
        expected_runtime = cast(dict[str, Any], expected["runtime"])
        expected_profile = cast(dict[str, str], expected["execution_profile"])
        destination = cast(dict[str, Any], exact["destination"])
        source_task = cast(dict[str, Any], exact["authoritative_source"])
        donor = cast(dict[str, Any], exact["runtime_attachment_donor"])
        current_host_session_id = str(
            session.metadata.get("current_host_session_id") or ""
        )
        host_history = [
            row
            for row in session.metadata.get("host_session_history", [])
            if isinstance(row, dict)
        ]
        host_history_ids = {
            str(row.get("host_session_id") or "") for row in host_history
        }
        require(
            project_id == destination["project_id"]
            and Path(str(destination["workspace_path"])).resolve()
            == Path(config.repository_path).resolve()
            and destination["task_id"]
            == cast(dict[str, Any], exact["host_context"])["current_task_id"],
            "DIRECT_STATE_TRAVEL_PROJECT_WORKSPACE_MISMATCH",
            "The fresh destination project/workspace binding does not match durable authority.",
            status="MISMATCH",
            writes_performed=False,
        )
        destination_history = [
            row
            for row in host_history
            if str(row.get("host_session_id") or "") == destination["task_id"]
        ]
        donor_current_before_entry = (
            current_host_session_id == donor["task_id"] and not destination_history
        )
        destination_boot_attached_before_entry = (
            current_host_session_id == destination["task_id"]
            and len(destination_history) == 1
            and str(destination_history[0].get("host") or "")
            == HostKind.CODEX_DESKTOP.value
            and not destination_history[0].get("binding_route")
        )
        require(
            source_task["task_id"] in host_history_ids
            and donor["task_id"] in host_history_ids
            and (donor_current_before_entry or destination_boot_attached_before_entry),
            "DIRECT_STATE_TRAVEL_HOST_HISTORY_MISMATCH",
            "Source, runtime donor, mandatory Boot attachment, and fresh destination do not match host history.",
            status="MISMATCH",
            current_host_session_id=current_host_session_id or None,
            destination_history_count=len(destination_history),
            writes_performed=False,
        )
        pointer_sha256 = sha256_bytes(canonical_json_bytes(pointer.as_dict()))
        require(
            pointer.accepted_pv == expected_pointer["accepted_pv"]
            and pointer.generation == expected_pointer["generation"]
            and pointer_sha256 == expected_pointer["pointer_sha256"]
            and pointer.accepted_pv == session.accepted_pv
            and pointer.generation == session.accepted_pointer_generation,
            "DIRECT_STATE_TRAVEL_POINTER_MISMATCH",
            "PV12/generation 12 is not the exact unchanged accepted-pointer baseline.",
            status="MISMATCH",
            writes_performed=False,
        )
        source_fields = (
            "branch",
            "commit_sha",
            "tree_sha",
            "worktree_sha256",
            "status_sha256",
            "tracked_diff_sha256",
            "dirty_path_set_sha256",
            "dirty_content_sha256",
            "status_record_count",
            "dirty_path_count",
        )
        source_mismatches = {
            field: {"expected": expected_source[field], "observed": source[field]}
            for field in source_fields
            if expected_source[field] != source[field]
        }
        require(
            not source_mismatches,
            "DIRECT_STATE_TRAVEL_DIRTY_SOURCE_MISMATCH",
            "The live branch, HEAD/tree, status, tracked diff, dirty path set, or dirty bytes changed.",
            status="MISMATCH",
            mismatches=source_mismatches,
            writes_performed=False,
        )
        require(
            prebootstrap["branch"] == source["branch"]
            and prebootstrap["commit_sha"] == source["commit_sha"]
            and prebootstrap["tree_sha"] == source["tree_sha"]
            and prebootstrap["captured_before_authorized_route_bootstrap"] is True,
            "DIRECT_STATE_TRAVEL_PREBOOTSTRAP_CONTINUITY_MISMATCH",
            "The observed pre-bootstrap dirty baseline is not on the same branch/HEAD/tree.",
            status="MISMATCH",
            writes_performed=False,
        )
        plan_fields = (
            "canonical_plan_sha256",
            "goal_projection_sha256",
            "history_projection_sha256",
            "snapshot_sha256",
            "row_start",
            "row_end",
            "task_count",
            "active_row",
            "active_task_id",
            "active_batch_id",
            "active_row_commit_batch_id",
            "active_batch_row_start",
            "active_batch_row_end",
            "host_window_row_start",
            "host_window_row_end",
            "next_hil_row",
            "next_hil_task_id",
            "physically_final_hil_row",
            "physically_final_hil_task_id",
        )
        plan_mismatches = {
            field: {"expected": expected_plan[field], "observed": plan[field]}
            for field in plan_fields
            if expected_plan[field] != plan[field]
        }
        require(
            not plan_mismatches,
            "DIRECT_STATE_TRAVEL_PLAN_MISMATCH",
            "The live Plan/active batch/1+9/HIL binding changed.",
            status="MISMATCH",
            mismatches=plan_mismatches,
            writes_performed=False,
        )
        plugin_fields = (
            "plugin_name",
            "plugin_version",
            "plugin_manifest_sha256",
            "routing_manifest_sha256",
            "identity_sha256",
            "tool_count",
            "read_tool_count",
            "write_tool_count",
        )
        plugin_mismatches = {
            field: {"expected": expected_plugin[field], "observed": plugin[field]}
            for field in plugin_fields
            if expected_plugin[field] != plugin[field]
        }
        profile_mismatches = execution_profile_mismatches(
            expected_profile,
            cast(dict[str, str], session.metadata.get("execution_profile") or {}),
        )
        runtime_instance_attestation = dict(self._runtime_instance_attestation)
        require(
            not plugin_mismatches
            and not profile_mismatches
            and expected_runtime["state"] == session.state.value
            and expected_runtime["generation"] == pointer.generation
            and expected_runtime["attachment_donor_task_id"] == donor["task_id"]
            and expected_runtime["runtime_instance_attestation_mode"]
            == "SERVER_DERIVED_ATTESTATION"
            and runtime_instance_attestation["status"] == "PASS"
            and flash.get("status") == "PASS",
            "DIRECT_STATE_TRAVEL_RUNTIME_PLUGIN_PROFILE_MISMATCH",
            "Installed plugin/catalog, runtime, Flash, or execution profile does not match.",
            status="MISMATCH",
            plugin_mismatches=plugin_mismatches,
            profile_mismatches=profile_mismatches,
            runtime_instance_attestation=runtime_instance_attestation,
            writes_performed=False,
        )
        direct_unsealed_work_state = (
            session.state
            in {
                SessionState.TASK_CLASSIFIED,
                # A failed Refresh can leave an otherwise intact classified
                # task in EXIT_BUILDING before any candidate is sealed.  That
                # is unfinished verified work, not an unaccepted HIL state,
                # and the direct route must carry it to the fresh destination
                # for bounded interrupted-exit recovery.
                SessionState.EXIT_BUILDING,
            }
            and session.candidate_id is None
            and not bool(session.metadata.get("pending_hil"))
            and not isinstance(session.metadata.get("pending_task"), dict)
        )
        candidate_state_preserved = (
            session.state in {SessionState.PV1_CANDIDATE, SessionState.PVN1_CANDIDATE}
            and bool(session.candidate_id)
            and bool(session.metadata.get("pending_hil"))
        )
        require(
            direct_unsealed_work_state or candidate_state_preserved,
            "DIRECT_STATE_TRAVEL_UNSUPPORTED_LIFECYCLE_STATE",
            "Direct destination entry requires classified work, an interrupted unsealed "
            "exit, or one intact candidate/pending-HIL state that will be preserved.",
            status="BLOCKED",
            lifecycle_state=session.state.value,
            interrupted_unsealed_exit=(
                session.state == SessionState.EXIT_BUILDING
                and session.candidate_id is None
            ),
            writes_performed=False,
        )
        candidate_validation: dict[str, Any] | None = None
        if candidate_state_preserved:
            candidate_validation = self.store.candidate_preservation_identity(
                project_id,
                str(session.candidate_id),
            )
            require(
                candidate_validation.get("candidate_id") == session.candidate_id
                and candidate_validation.get("status") == "PASS",
                "DIRECT_STATE_TRAVEL_CANDIDATE_INTEGRITY_MISMATCH",
                "The preserved candidate failed its exact package integrity validation.",
                status="MISMATCH",
                writes_performed=False,
            )
        candidate_before = {
            "candidate_id": session.candidate_id,
            "lifecycle_state": session.state.value,
            "pending_hil": bool(session.metadata.get("pending_hil")),
            "pending_task_sha256": (
                sha256_bytes(
                    canonical_json_bytes(
                        cast(dict[str, Any], session.metadata["pending_task"])
                    )
                )
                if isinstance(session.metadata.get("pending_task"), dict)
                else None
            ),
            "candidate_manifest_sha256": (
                candidate_validation.get("manifest_sha256")
                if candidate_validation
                else None
            ),
            "candidate_package_sha256": (
                candidate_validation.get("package_sha256")
                if candidate_validation
                else None
            ),
        }
        stale_sealed = session.metadata.get("state_travel")
        if isinstance(stale_sealed, dict) and stale_sealed.get("status") == "PREPARED":
            require(
                stale_sealed.get("origin_host_session_id") != source_task["task_id"],
                "DIRECT_STATE_TRAVEL_ELIGIBLE_SEALED_HANDOFF_PRESENT",
                "An eligible fresh sealed handoff exists; the direct route cannot bypass it.",
                status="BLOCKED",
                writes_performed=False,
            )

        previous_continuity_value = session.metadata.get("runtime_continuity")
        require(
            isinstance(previous_continuity_value, dict),
            "DIRECT_STATE_TRAVEL_RUNTIME_CONTINUITY_REQUIRED",
            "Direct entry requires the already-sealed accepted-pointer continuity proof.",
            status="MISMATCH",
            writes_performed=False,
        )
        previous_continuity = validate_runtime_continuity(
            cast(dict[str, Any], previous_continuity_value)
        )
        previous_entry_pointer = cast(
            dict[str, Any], previous_continuity.get("entry_pointer") or {}
        )
        prior_pointer_matches = bool(
            previous_entry_pointer.get("accepted_pv") == pointer.accepted_pv
            and previous_entry_pointer.get("pointer_generation") == pointer.generation
            and previous_entry_pointer.get("accepted_manifest_sha256")
            == pointer.accepted_manifest_sha256
        )
        now = utc_now()
        runtime_continuity = build_runtime_continuity(
            project_id=project_id,
            governed_session_id=session.session_id,
            workspace_id=session.workspace_id,
            host=HostKind.CODEX_DESKTOP,
            host_session_id=str(destination["task_id"]),
            ephemeral=False,
            persistence_route=persistence_route,
            flash=flash,
            accepted_pv=pointer.accepted_pv,
            pointer_generation=pointer.generation,
            accepted_manifest_sha256=pointer.accepted_manifest_sha256,
            accepted_package_sha256=(
                str(previous_entry_pointer.get("accepted_package_sha256") or "")
                or None
                if prior_pointer_matches
                else None
            ),
            accepted_promotable_under_current_rules=(
                previous_entry_pointer.get("promotable_under_current_rules")
                if prior_pointer_matches
                else None
            ),
            accepted_validation_scope=(
                "PRIOR_SEALED_POINTER_BASELINE_REUSED"
                if prior_pointer_matches
                else "POINTER_BASELINE_IDENTITY_ONLY_NO_ACCEPTED_ARCHIVE_QUERY"
            ),
            accepted_artifact_available=(
                previous_entry_pointer.get("accepted_artifact_available")
                if prior_pointer_matches
                else None
            ),
            accepted_archive_queried=False,
            host_entry_consumption=None,
        )
        if previous_continuity:
            session.metadata.setdefault(
                "runtime_continuity_receipt_archive", []
            ).append(
                {
                    "continuity_receipt_sha256": previous_continuity.get(
                        "continuity_receipt_sha256"
                    ),
                    "receipt": previous_continuity,
                    "disposition": "SUPERSEDED_BY_DIRECT_FORCED_SAME_WORKTREE_ENTRY",
                    "archived_at": now,
                }
            )
        session.host = HostKind.CODEX_DESKTOP
        session.metadata["current_host_session_id"] = destination["task_id"]
        session.metadata.setdefault("host_session_history", []).append(
            {
                "host_session_id": destination["task_id"],
                "host": HostKind.CODEX_DESKTOP.value,
                "bound_at": now,
                "binding_route": exact["route"],
            }
        )
        session.metadata["persistence_mode"] = persistence_mode
        session.metadata["persistence_route"] = dict(persistence_route)
        session.metadata["runtime_continuity"] = runtime_continuity
        session.metadata.setdefault("runtime_continuity_history", []).append(
            {
                "host": HostKind.CODEX_DESKTOP.value,
                "host_session_id": destination["task_id"],
                "continuity_receipt_sha256": runtime_continuity[
                    "continuity_receipt_sha256"
                ],
                "bound_at": now,
                "binding_route": exact["route"],
            }
        )
        session.metadata["ephemeral_host"] = False
        session.metadata["server_has_durable_filesystem"] = (
            server_has_durable_filesystem
        )
        session.metadata["client_source_edit_authority"] = self._source_edit_authority(
            HostKind.CODEX_DESKTOP, client_can_edit_source
        )
        session.metadata["execution_profile"] = expected_profile
        session.metadata["host_execution_profile_mutation_supported"] = False

        calling_task_binding_authority = self._direct_entry_task_binding_authority(
            session,
            project_id=project_id,
            destination=destination,
            exact_binding=exact,
            plan=plan,
            runtime_instance_attestation=runtime_instance_attestation,
            rebound_at=now,
        )

        pending_task_after_sha256 = (
            sha256_bytes(
                canonical_json_bytes(
                    cast(dict[str, Any], session.metadata["pending_task"])
                )
            )
            if isinstance(session.metadata.get("pending_task"), dict)
            else None
        )
        require(
            session.candidate_id == candidate_before["candidate_id"]
            and session.state.value == candidate_before["lifecycle_state"]
            and bool(session.metadata.get("pending_hil"))
            == candidate_before["pending_hil"]
            and pending_task_after_sha256 == candidate_before["pending_task_sha256"],
            "DIRECT_STATE_TRAVEL_CANDIDATE_PRESERVATION_MISMATCH",
            "Direct entry changed the candidate, pending HIL, or pending task.",
            status="MISMATCH",
            writes_performed=False,
        )

        destination_orchestration = build_direct_destination_orchestration(exact)

        receipt_body = {
            "schema": "evidence-lane.direct-forced-same-worktree-entry-receipt.v2",
            "status": "PASS",
            "route": exact["route"],
            "request_nonce_sha256": sha256_bytes(nonce.encode("utf-8")),
            "project_id": project_id,
            "session_id": session_id,
            "live_dirty_source_proof": source,
            "prebootstrap_dirty_source_proof": prebootstrap,
            "accepted_pointer_baseline": {
                **pointer.as_dict(),
                "pointer_sha256": pointer_sha256,
                "moved": False,
            },
            "plan_task_proof": plan,
            "preserved_candidate_hil_state": {
                **candidate_before,
                "preservation_mode": (
                    "EXISTING_CANDIDATE_PENDING_HIL_PRESERVED"
                    if candidate_state_preserved
                    else "NO_CANDIDATE_UNSEALED_WORK_PRESERVED"
                ),
                "candidate_cleared": False,
                "candidate_rebuilt": False,
                "pending_hil_cleared": False,
                "pending_task_cleared": False,
            },
            "runtime_plugin_profile_proof": {
                "plugin": plugin,
                "execution_profile": expected_profile,
                "runtime_state": session.state.value,
                "runtime_instance_attestation": runtime_instance_attestation,
                "flash_authority_version": flash.get("authority_version"),
                "flash_authority_digest": flash.get("authority_digest"),
                "hooks_mode": expected_runtime["hooks_mode"],
            },
            "host_task_binding": {
                "authoritative_source": source_task,
                "runtime_attachment_donor": donor,
                "destination": destination,
                "destination_boot_attached_before_direct_binding": (
                    destination_boot_attached_before_entry
                ),
                "fresh_local_task": True,
                "fork": False,
                "continued_from_chat": False,
            },
            "calling_task_binding_authority": {
                "schema": calling_task_binding_authority["schema"],
                "status": calling_task_binding_authority["status"],
                "receipt_sha256": calling_task_binding_authority["receipt_sha256"],
                "active_plan_task_id": calling_task_binding_authority[
                    "active_plan_task_id"
                ],
                "runtime_task_id": calling_task_binding_authority["runtime_task_id"],
                "host_task_id": calling_task_binding_authority["host_task_id"],
                "authority_route": calling_task_binding_authority["authority_route"],
                "runtime_instance_attestation_receipt_sha256": (
                    calling_task_binding_authority["direct_entry_authority"][
                        "runtime_instance_attestation_receipt_sha256"
                    ]
                ),
            },
            "destination_orchestration": destination_orchestration,
            "sealed_transport": {
                **cast(dict[str, Any], exact["sealed_transport"]),
                "stale_prepared_receipt_preserved_unconsumed": isinstance(
                    stale_sealed, dict
                ),
            },
            "no_mutation_flags": {
                "source_mutated": False,
                "candidate_created": False,
                "existing_candidate_preserved": candidate_state_preserved,
                "pending_hil_preserved": bool(session.metadata.get("pending_hil")),
                "candidate_cleared": False,
                "candidate_rebuilt": False,
                "hil_inferred": False,
                "pointer_moved": False,
                "sealed_prepare_called": False,
                "sealed_resume_called": False,
                "git_executed": False,
                "install_executed": False,
            },
            "bound_at": now,
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        session.metadata["direct_forced_same_worktree_entry"] = receipt
        self._save(session)
        runtime_activation = self.runtime_activation.activate(
            project_id=project_id,
            session_id=session_id,
            host_session_id=str(destination["task_id"]),
            flash=flash,
        )
        ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="state_travel.direct_forced_same_worktree.bound",
            visible_payload=receipt,
            occurred_at=now,
            session_id=session_id,
            task_id=str(session.metadata.get("active_backlog_task_id") or "") or None,
            run_id=str(session.metadata.get("run_id") or "") or None,
            event_id=f"direct_{sha256_bytes(nonce.encode('utf-8'))[:24].lower()}",
        )
        atomic_write_json(
            journal_path,
            {
                "schema": "evidence-lane.direct-forced-same-worktree-entry-journal.v1",
                "status": "COMMITTED",
                "request_nonce": nonce,
                "receipt": receipt,
            },
        )
        return {
            "status": "PASS",
            "direct_state_travel": receipt,
            "runtime_activation": runtime_activation,
            "session": self.load(project_id, session_id).as_dict(),
        }

    def begin_next_turn(
        self,
        project_id: str,
        session_id: str,
        *,
        continue_same_host: bool = False,
        continuation_reason: str | None = None,
    ) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        require(
            session.state in {SessionState.PVN_ACCEPTED, SessionState.PVN1_ACCEPTED},
            "NEXT_TURN_STATE_INVALID",
            "A follow-up turn begins only from a newly accepted PV.",
            status="BLOCKED",
            state=session.state.value,
        )
        require(
            continue_same_host
            and continuation_reason == "EXPLICIT_USER_CONTINUATION",
            "SAME_HOST_CONTINUATION_EXPLICIT_REQUIRED",
            "Beginning another turn in the same task requires explicit user continuation.",
            status="BLOCKED",
        )
        pointer = self.store.pointer(project_id)
        require(
            pointer.accepted_pv is not None,
            "NEXT_TURN_ACCEPTED_PV_MISSING",
            "Direct handoff requires the newly accepted immutable PV.",
            status="MISMATCH",
        )
        validation = self.accepted_entry_validation(
            project_id,
            cast(str, pointer.accepted_pv),
        )
        freshness = self._current_live_root_freshness(project_id)
        session.accepted_pv = pointer.accepted_pv
        session.accepted_pointer_generation = pointer.generation
        session.task = None
        session.candidate_id = None
        session.metadata["turn"] = int(session.metadata.get("turn", 1)) + 1
        session.metadata.pop("run_id", None)
        session.metadata.pop("source_update_confirmed", None)
        session.metadata["next_candidate_would_be"] = self.store.next_pv_id(project_id)
        session.metadata["entry_pv"] = pointer.accepted_pv
        session.metadata["entry_manifest_sha256"] = validation["manifest_sha256"]
        session.metadata["entry_package_sha256"] = validation["package_sha256"]
        session.metadata["entry_freshness"] = freshness
        session.metadata["current_accepted_freshness"] = freshness
        session.metadata["source_state"] = (
            "ACCEPTED_ENTRY_EXACT"
            if freshness["state"] == "FRESH"
            else "ACCEPTED_ENTRY_STALE_OR_DIFFERENT_LIVE_SOURCE"
        )
        session.metadata["accepted_pv_query_scope"] = (
            "CURRENT_ENTRY_AND_LIVE_SOURCE_EXACT"
            if freshness["state"] == "FRESH"
            else "IMMUTABLE_ENTRY_STATE_ONLY_LIVE_SOURCE_DIFFERS"
        )
        entry_receipt = {
            "turn": session.metadata["turn"],
            "entry_pv": pointer.accepted_pv,
            "pointer_generation": pointer.generation,
            "manifest_sha256": validation["manifest_sha256"],
            "package_sha256": validation["package_sha256"],
            "host_session_id": session.metadata.get("current_host_session_id"),
            "entered_at": utc_now(),
        }
        session.metadata.setdefault("entry_history", []).append(entry_receipt)
        session.state = transition(
            session.state,
            LifecycleEvent.BEGIN_NEXT_TURN,
            SessionState.PVN1_ENTRY,
        )
        self._save(session)
        event = ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="pv.next_entry",
            visible_payload={
                "accepted_entry_pv": pointer.accepted_pv,
                "pointer_generation": pointer.generation,
                "next_candidate_would_be": self.store.next_pv_id(project_id),
                "entry_manifest_sha256": validation["manifest_sha256"],
                "entry_package_sha256": validation["package_sha256"],
                "entry_freshness": freshness,
                "entry_slip_internal": True,
            },
            occurred_at=utc_now(),
            session_id=session_id,
        )
        return {
            "status": "PASS",
            "session": session.as_dict(),
            "proof": event["visible_payload"],
            "entry_receipt": entry_receipt,
        }

    def close(self, project_id: str, session_id: str, *, reason: str) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        require(
            bool(reason.strip()),
            "SESSION_CLOSE_REASON_REQUIRED",
            "Closing a governed session requires a visible reason.",
            status="BLOCKED",
        )
        runtime_activation = self.runtime_activation.detach(
            project_id=project_id,
            session_id=session_id,
            reason=reason.strip(),
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
        return {
            "status": "PASS",
            "session": session.as_dict(),
            "event": event,
            "runtime_activation": runtime_activation,
            "plugin_installation_preserved": True,
            "immutable_store_preserved": True,
        }
