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
    state_travel_next_action,
)
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
    additive_deltas_from_task_list,
    build_direct_destination_orchestration,
    execution_profile_from_context,
    execution_profile_mismatches,
    normalize_additive_deltas,
    normalize_destination_host_continuity,
    normalize_direct_forced_same_worktree_binding,
    normalize_task_list,
    require_unfinished_execution_profile,
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
_FALLBACK_PREWARM_TASK_ID = "EL-CODEX-PV11-FALLBACK-SLOT-INSTALL-PREWARM-DELTA-149"
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
    if normalized.isdigit():
        normalized = f"PV{normalized}"
    require(
        normalized.startswith("PV")
        and normalized[2:].isdigit()
        and int(normalized[2:]) >= 1,
        "ROLLBACK_TARGET_INVALID",
        "Rollback target must be an accepted ordinal such as PV2 or 2.",
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
                "kind": "EXPLICIT_PV",
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
                    and session.metadata.get("current_host_session_id")
                    == host_task_id,
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
                    and session.metadata.get("current_host_session_id")
                    == host_task_id,
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
                        cast(dict[str, Any], prior_active_rebind).get(
                            "receipt_sha256"
                        )
                        or ""
                    )
                    if isinstance(prior_active_rebind, dict)
                    else None
                )
                direct_entry = session.metadata.get(
                    "direct_forced_same_worktree_entry"
                )
                direct_entry_receipt_sha256 = (
                    str(
                        cast(dict[str, Any], direct_entry).get("receipt_sha256")
                        or ""
                    )
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
                    "recovery_binding_contract": {
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
                    "validation_scope": "LIVE_ROOT_PROMOTION_RECEIPT_PAIR",
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
            and int(entry_pointer.get("pointer_generation") or -1)
            == pointer.generation
            and entry_pointer.get("accepted_manifest_sha256")
            == pointer.accepted_manifest_sha256
            and entry_pointer.get("accepted_authority_integrity_validated") is True
        )
        if not entry_pointer_matches:
            promoted = self.store.live_root_pointer_continuity(project_id, pv_id)
            require(
                exact_session.candidate_id is None
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
                "validation_scope": "LIVE_ROOT_PROMOTION_RECEIPT_PAIR",
                "accepted_artifact_available": None,
                "accepted_artifact_integrity_validated": False,
                "accepted_archive_queried": False,
                "continuity_reference_integrity_validated": True,
                "runtime_continuity_receipt_sha256": continuity[
                    "continuity_receipt_sha256"
                ],
                "promotion_receipt_sha256": promoted[
                    "promotion_receipt_sha256"
                ],
                "swap_journal_sha256": promoted["swap_journal_sha256"],
                "accepted_storage": {
                    "status": "NOT_QUERIED",
                    "state": "POINTER_AND_ROOT_RECEIPT_REFERENCE_ONLY",
                    "accepted_archive_queried": False,
                },
            }
        package_sha256 = str(
            entry_pointer.get("accepted_package_sha256") or ""
        ).strip().upper()
        require(
            len(package_sha256) == 64
            and all(character in "0123456789ABCDEF" for character in package_sha256),
            "ACCEPTED_CONTINUITY_PACKAGE_HASH_INVALID",
            "The prior continuity receipt has no exact accepted package hash.",
            status="MISMATCH",
            project_id=project_id,
            pv_id=pv_id,
        )
        if exact_session.candidate_id is not None:
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

    def _verify_state_travel_task_advance(
        self,
        project_id: str,
        session_id: str,
        *,
        completed_backlog_task_id: str,
        replacement_backlog_task_id: str,
        replacement_task: TaskContract,
        native_route_receipt: dict[str, Any] | None,
        installed_surface_inventory: dict[str, Any] | None,
        project_panel_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Seal the exact non-promoting proof for a consumed handoff row.

        State Travel is the implementation and acceptance condition for this
        narrow row type.  The proof is intentionally gathered from the active
        native MCP process, the installed package, the read-only project panel,
        the live runtime binding, the Plan Lane, and the actual remote ``main``
        ref before either Plan status is changed.
        """

        session = self.load(project_id, session_id)
        pointer = self.store.pointer(project_id)
        travel = session.metadata.get("state_travel")
        require(
            isinstance(travel, dict)
            and travel.get("status") == "VERIFIED_RESUME_READY"
            and travel.get("continuation_ready") is True
            and travel.get("project_id") == project_id
            and travel.get("session_id") == session_id,
            "STATE_TRAVEL_TASK_ADVANCE_VERIFIED_HANDOFF_REQUIRED",
            "The active handoff row may advance only from one consumed, resume-ready State Travel receipt.",
            status="BLOCKED",
        )
        travel = cast(dict[str, Any], travel)
        handoff_id = str(travel.get("handoff_id") or "").strip()
        destination_host_session_id = str(
            travel.get("destination_host_session_id") or ""
        ).strip()
        current_host_session_id = str(
            session.metadata.get("current_host_session_id") or ""
        ).strip()
        matching_history = [
            row
            for row in session.metadata.get("state_travel_history", [])
            if isinstance(row, dict)
            and row.get("handoff_id") == handoff_id
            and row.get("status") == "VERIFIED_RESUME_READY"
            and row.get("destination_host_session_id") == destination_host_session_id
        ]
        require(
            bool(handoff_id)
            and len(matching_history) == 1
            and destination_host_session_id == current_host_session_id
            and travel.get("boot_verified") is True
            and travel.get("flash_verified") is True
            and travel.get("pointer_verified") is True
            and travel.get("plan_lane_verified") is True
            and travel.get("live_source_verified") is True
            and travel.get("execution_profile_verified") is True
            and travel.get("host_settings_mutated") is False,
            "STATE_TRAVEL_TASK_ADVANCE_HANDOFF_IDENTITY_MISMATCH",
            "The consumed State Travel receipt, destination host session, or entry verification is not exact.",
            status="MISMATCH",
            handoff_id=handoff_id or None,
            matching_history_count=len(matching_history),
            destination_host_session_id=destination_host_session_id or None,
            current_host_session_id=current_host_session_id or None,
        )
        require(
            session.state == SessionState.TASK_CLASSIFIED
            and session.candidate_id is None
            and not session.metadata.get("pending_hil")
            and session.metadata.get("active_backlog_task_id")
            == completed_backlog_task_id
            and session.metadata.get("active_backlog_task_status") == "DONE"
            and session.metadata.get("client_source_edit_authority") == "DIRECT"
            and isinstance(session.task, dict),
            "STATE_TRAVEL_TASK_ADVANCE_SESSION_BOUNDARY_MISMATCH",
            "The session is not at the exact completed handoff-row boundary.",
            status="MISMATCH",
            state=session.state.value,
            active_backlog_task_id=session.metadata.get("active_backlog_task_id"),
            active_backlog_task_status=session.metadata.get(
                "active_backlog_task_status"
            ),
            candidate_id=session.candidate_id,
            pending_hil=bool(session.metadata.get("pending_hil")),
            client_source_edit_authority=session.metadata.get(
                "client_source_edit_authority"
            ),
        )
        require(
            pointer.accepted_pv is not None
            and pointer.accepted_pv == session.accepted_pv
            and pointer.accepted_pv == travel.get("accepted_pv")
            and pointer.generation == session.accepted_pointer_generation
            and pointer.generation == travel.get("pointer_generation")
            and pointer.accepted_manifest_sha256 == travel.get("manifest_sha256"),
            "STATE_TRAVEL_TASK_ADVANCE_POINTER_MISMATCH",
            "The accepted pointer changed after the verified handoff entry.",
            status="STALE",
            pointer=pointer.as_dict(),
        )

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
        require(
            runtime_status.get("state") == "ACTIVE"
            and runtime_status.get("prompt_capture_active") is True
            and runtime_status.get("visible_response_capture_active") is True
            and isinstance(runtime_binding, dict)
            and destination_host_session_id
            in runtime_binding.get("host_session_ids", []),
            "STATE_TRAVEL_TASK_ADVANCE_RUNTIME_BINDING_MISMATCH",
            "The live runtime is not attached to the exact destination host session.",
            status="MISMATCH",
            runtime_state=runtime_status.get("state"),
            destination_host_session_id=destination_host_session_id,
        )

        route = native_route_receipt or {}
        require(
            route.get("schema") == "evidence-lane.native-mcp-route-receipt.v1"
            and route.get("status") == "PASS"
            and route.get("server_identity") == "evidence-lane"
            and route.get("canonical_tool_namespace") == "mcp__evidence_lane__"
            and route.get("exposure_profile") == "FULL_LIFECYCLE"
            and route.get("tool_count") == NATIVE_TOOL_COUNT
            and route.get("tool_names_unique") is True
            and route.get("project_route_argument_required") is True
            and route.get("cross_project_fallback_allowed") is False
            and len(str(route.get("tool_catalog_sha256") or "")) == 64,
            "STATE_TRAVEL_TASK_ADVANCE_NATIVE_ROUTE_MISMATCH",
            "The closeout must execute through the exact current native Evidence Lane route.",
            status="MISMATCH",
        )
        surface = installed_surface_inventory or {}
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
        require(
            surface.get("schema")
            == "evidence-lane.codex-installed-surface-inventory.v2"
            and str(surface.get("plugin_version") or "").split("+", 1)[0]
            == ENGINE_VERSION
            and surface.get("catalog") == expected_catalog
            and isinstance(surface.get("skills"), dict)
            and surface["skills"].get("count") == GOVERNED_SKILL_COUNT
            and surface.get("raw_paths_included") is False
            and surface.get("surface_inventory_sha256")
            == sha256_bytes(canonical_json_bytes(surface_core)),
            "STATE_TRAVEL_TASK_ADVANCE_INSTALLED_SURFACE_MISMATCH",
            "The installed plugin surface does not prove the exact v2 catalog and skill inventory.",
            status="MISMATCH",
            expected_catalog=expected_catalog,
        )

        panel = project_panel_snapshot or {}
        panel_facts = {
            str(row.get("label")): row.get("value")
            for row in panel.get("facts", [])
            if isinstance(row, dict)
        }
        panel_hil = panel.get("hil")
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
            and panel_hil.get("candidate") is None
            and panel_hil.get("decision_state") == "NO_PENDING_CANDIDATE",
            "STATE_TRAVEL_TASK_ADVANCE_PROJECT_PANEL_MISMATCH",
            "The native project panel does not match the candidate-free accepted pointer boundary.",
            status="MISMATCH",
        )

        backlog = self.store.backlog_status(project_id)
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
        resume_contract = cast(dict[str, Any], travel.get("resume_contract") or {})
        resume_step = resume_contract.get("resume_step")
        resume_row = next(
            (
                row
                for row in resume_contract.get("task_list", [])
                if isinstance(row, dict) and row.get("number") == resume_step
            ),
            None,
        )
        final_contract_row = next(
            reversed(
                [
                    row
                    for row in resume_contract.get("task_list", [])
                    if isinstance(row, dict)
                ]
            ),
            None,
        )
        tasks_by_id = {str(row.get("task_id")): row for row in backlog.get("tasks", [])}
        completed_plan_task = tasks_by_id.get(completed_backlog_task_id)
        replacement_plan_task = tasks_by_id.get(replacement_backlog_task_id)
        plan_before = (
            len(active) == 1
            and active[0].get("task_id") == completed_backlog_task_id
            and isinstance(first_queued, dict)
            and first_queued.get("task_id") == replacement_backlog_task_id
            and isinstance(completed_plan_task, dict)
            and completed_plan_task.get("status") == "ACTIVE"
            and isinstance(replacement_plan_task, dict)
            and replacement_plan_task.get("status") == "QUEUED"
        )
        plan_after = (
            len(active) == 1
            and active[0].get("task_id") == replacement_backlog_task_id
            and isinstance(completed_plan_task, dict)
            and completed_plan_task.get("status") == "DONE"
            and isinstance(
                completed_plan_task.get("state_travel_completion_receipt"),
                dict,
            )
            and isinstance(replacement_plan_task, dict)
            and replacement_plan_task.get("status") == "ACTIVE"
            and replacement_plan_task.get("active_session_id") == session_id
            and replacement_plan_task.get("runtime_task_id") == replacement_task.task_id
        )
        require(
            backlog.get("status") == "PASS"
            and goal.get("canonical_authority") == "PLAN_LANE"
            and goal.get("persistent_until") == "NEXT_SIX_WAY_HIL_PRESENTED"
            and bool(rows)
            and numbers == list(range(numbers[0], numbers[0] + len(numbers)))
            and (plan_before or plan_after)
            and isinstance(resume_row, dict)
            and resume_row.get("task_id") == completed_backlog_task_id
            and resume_row.get("status") == "IN_PROGRESS"
            and isinstance(final_contract_row, dict)
            and rows[-1].get("task_id") == final_contract_row.get("task_id")
            and rows[-1].get("panel_role")
            == final_contract_row.get("panel_role")
            == "PHYSICALLY_FINAL_HIL",
            "STATE_TRAVEL_TASK_ADVANCE_PLAN_MISMATCH",
            "The current Plan is not the contiguous one-active projection sealed by the handoff.",
            status="MISMATCH",
            active_task_ids=[row.get("task_id") for row in active],
            plan_before=plan_before,
            plan_after=plan_after,
            first_queued_task_id=(
                first_queued.get("task_id") if isinstance(first_queued, dict) else None
            ),
        )

        config = self.store.config(project_id)
        source = identity_json(
            inspect_repository(
                config.repository_path,
                expected_owner=config.expected_owner,
                expected_name=config.expected_name,
            ),
            config.repository_path,
        )
        entry_source = cast(dict[str, Any], travel.get("source_snapshot") or {})
        remote_main_result = run_git(
            config.repository_path,
            ["ls-remote", "--exit-code", "origin", "refs/heads/main"],
        )
        remote_main_rows = [
            line.split()
            for line in remote_main_result.stdout.splitlines()
            if line.strip()
        ]
        remote_main_sha = (
            remote_main_rows[0][0].lower()
            if len(remote_main_rows) == 1 and len(remote_main_rows[0]) >= 2
            else ""
        )
        porcelain = run_git(
            config.repository_path,
            ["status", "--porcelain=v1", "--untracked-files=all"],
        ).stdout
        tracked_deletions = [
            line[:2]
            for line in porcelain.splitlines()
            if len(line) >= 2 and "D" in line[:2]
        ]
        require(
            len(remote_main_sha) == 40
            and all(character in "0123456789abcdef" for character in remote_main_sha)
            and remote_main_sha == str(source.get("commit_sha") or "").lower()
            and str(source.get("branch") or "") not in {"", "main", "DETACHED"}
            and str(source.get("tree_sha") or "").lower()
            == str(entry_source.get("tree_sha") or "").lower()
            and not tracked_deletions,
            "STATE_TRAVEL_TASK_ADVANCE_SOURCE_MISMATCH",
            "The task branch is not based on exact remote main, the entry tree changed, or tracked files were deleted.",
            status="MISMATCH",
            current_branch=source.get("branch"),
            current_commit=source.get("commit_sha"),
            remote_main_commit=remote_main_sha or None,
            entry_tree=entry_source.get("tree_sha"),
            current_tree=source.get("tree_sha"),
            tracked_deletion_count=len(tracked_deletions),
        )

        prior_task = cast(dict[str, Any], session.task)
        prior_runtime_task_id = str(prior_task.get("task_id") or "").strip()
        require(
            bool(prior_runtime_task_id),
            "STATE_TRAVEL_TASK_ADVANCE_PRIOR_RUNTIME_TASK_REQUIRED",
            "The handoff row has no runtime task identity.",
            status="MISMATCH",
        )
        if plan_after:
            existing_receipt = cast(
                dict[str, Any],
                cast(dict[str, Any], completed_plan_task)[
                    "state_travel_completion_receipt"
                ],
            )
            existing_body = {
                key: value
                for key, value in existing_receipt.items()
                if key != "receipt_sha256"
            }
            require(
                existing_receipt.get("receipt_sha256")
                == sha256_bytes(canonical_json_bytes(existing_body))
                and existing_receipt.get("schema")
                == "evidence-lane.verified-state-travel-task-advance.v1"
                and existing_receipt.get("project_id") == project_id
                and existing_receipt.get("session_id") == session_id
                and existing_receipt.get("handoff_id") == handoff_id
                and existing_receipt.get("completed_backlog_task_id")
                == completed_backlog_task_id
                and existing_receipt.get("replacement_backlog_task_id")
                == replacement_backlog_task_id
                and existing_receipt.get("prior_runtime_task_id")
                == prior_runtime_task_id
                and existing_receipt.get("replacement_runtime_task_id")
                == replacement_task.task_id
                and existing_receipt.get("accepted_pv") == pointer.accepted_pv
                and existing_receipt.get("pointer_generation") == pointer.generation
                and existing_receipt.get("manifest_sha256")
                == pointer.accepted_manifest_sha256
                and existing_receipt.get("native_route", {}).get("tool_catalog_sha256")
                == route.get("tool_catalog_sha256")
                and existing_receipt.get("installed_surface", {}).get(
                    "surface_inventory_sha256"
                )
                == surface.get("surface_inventory_sha256")
                and existing_receipt.get("project_panel_sha256")
                == sha256_bytes(canonical_json_bytes(panel))
                and existing_receipt.get("current_source_identity_sha256")
                == sha256_bytes(canonical_json_bytes(source))
                and existing_receipt.get("current_worktree_sha256")
                == source.get("worktree_sha256")
                and existing_receipt.get("current_porcelain_sha256")
                == sha256_bytes(porcelain.encode("utf-8"))
                and existing_receipt.get("remote_main_commit") == remote_main_sha
                and existing_receipt.get("candidate_created") is False
                and existing_receipt.get("pending_hil") is False
                and existing_receipt.get("pointer_moved") is False
                and existing_receipt.get("hil_inferred") is False,
                "STATE_TRAVEL_TASK_ADVANCE_PLAN_RECOVERY_MISMATCH",
                "A partially committed Plan advance does not match the exact live route, source, pointer, or successor contract.",
                status="MISMATCH",
            )
            return {
                "receipt": existing_receipt,
                "prior_task": prior_task,
                "source": source,
                "backlog": backlog,
                "plan_recovery": True,
            }
        body = {
            "schema": "evidence-lane.verified-state-travel-task-advance.v1",
            "project_id": project_id,
            "session_id": session_id,
            "handoff_id": handoff_id,
            "handoff_sha256": travel.get("handoff_sha256"),
            "state_travel_status": travel.get("status"),
            "state_travel_completed_at": travel.get("completed_at"),
            "destination_host": travel.get("destination_host"),
            "destination_host_session_id": destination_host_session_id,
            "completed_backlog_task_id": completed_backlog_task_id,
            "replacement_backlog_task_id": replacement_backlog_task_id,
            "prior_runtime_task_id": prior_runtime_task_id,
            "prior_run_id": session.metadata.get("run_id"),
            "replacement_runtime_task_id": replacement_task.task_id,
            "successor_classified_at": utc_now(),
            "accepted_pv": pointer.accepted_pv,
            "pointer_generation": pointer.generation,
            "manifest_sha256": pointer.accepted_manifest_sha256,
            "entry_plan_snapshot_sha256": travel.get("plan_snapshot", {}).get(
                "snapshot_sha256"
            ),
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
            "project_panel_sha256": sha256_bytes(canonical_json_bytes(panel)),
            "runtime_activation_generation": runtime_status.get("generation"),
            "runtime_host_session_verified": True,
            "entry_source_identity_sha256": entry_source.get("identity_sha256"),
            "current_source_identity_sha256": sha256_bytes(
                canonical_json_bytes(source)
            ),
            "current_worktree_sha256": source.get("worktree_sha256"),
            "current_porcelain_sha256": sha256_bytes(porcelain.encode("utf-8")),
            "remote_main_commit": remote_main_sha,
            "task_branch": source.get("branch"),
            "entry_tree_preserved": True,
            "tracked_deletions_absent": True,
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
            "source": source,
            "backlog": backlog,
        }

    def _append_state_travel_task_advance_lineage(
        self,
        project_id: str,
        session_id: str,
        *,
        session: SessionRecord,
        task: TaskContract,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        """Idempotently complete both visible lineage events for one advance."""

        receipt_sha256 = str(receipt.get("receipt_sha256") or "").strip()
        require(
            len(receipt_sha256) == 64
            and bool(str(receipt.get("successor_classified_at") or "").strip()),
            "STATE_TRAVEL_TASK_ADVANCE_LINEAGE_RECEIPT_INVALID",
            "The state-travel advance receipt cannot bind deterministic lineage events.",
            status="MISMATCH",
        )
        event_suffix = receipt_sha256[:32].lower()
        lineage = ChatLineage(self._lineage_path(project_id, session_id))
        completion_event = lineage.append(
            event_type="task.state_travel_handoff.completed",
            visible_payload=receipt,
            occurred_at=str(receipt.get("state_travel_completed_at") or ""),
            session_id=session_id,
            task_id=str(receipt["prior_runtime_task_id"]),
            run_id=(
                str(receipt.get("prior_run_id"))
                if receipt.get("prior_run_id") is not None
                else None
            ),
            event_id=f"evt_st_advance_completed_{event_suffix}",
        )
        classification_event = lineage.append(
            event_type="task.classified",
            visible_payload=task.as_dict(),
            occurred_at=str(receipt["successor_classified_at"]),
            session_id=session_id,
            task_id=task.task_id,
            run_id=str(session.metadata["run_id"]),
            event_id=f"evt_st_advance_classified_{event_suffix}",
        )
        return {
            "completion_event": completion_event,
            "classification_event": classification_event,
        }

    def _replay_state_travel_task_advance(
        self,
        project_id: str,
        session_id: str,
        *,
        task: TaskContract,
        receipt: dict[str, Any],
        native_route_receipt: dict[str, Any] | None,
        installed_surface_inventory: dict[str, Any] | None,
        project_panel_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Return one exact retry without appending another Plan or lineage event."""

        session = self.load(project_id, session_id)
        receipt_body = {
            key: value for key, value in receipt.items() if key != "receipt_sha256"
        }
        route = native_route_receipt or {}
        surface = installed_surface_inventory or {}
        panel = project_panel_snapshot or {}
        pointer = self.store.pointer(project_id)
        require(
            receipt.get("schema")
            == "evidence-lane.verified-state-travel-task-advance.v1"
            and receipt.get("project_id") == project_id
            and receipt.get("session_id") == session_id
            and receipt.get("replacement_backlog_task_id")
            == session.metadata.get("active_backlog_task_id")
            and receipt.get("replacement_runtime_task_id") == task.task_id
            and receipt.get("receipt_sha256")
            == sha256_bytes(canonical_json_bytes(receipt_body))
            and session.task == task.as_dict()
            and session.candidate_id is None
            and not session.metadata.get("pending_hil")
            and pointer.accepted_pv == receipt.get("accepted_pv")
            and pointer.generation == receipt.get("pointer_generation")
            and pointer.accepted_manifest_sha256 == receipt.get("manifest_sha256"),
            "STATE_TRAVEL_TASK_ADVANCE_REPLAY_MISMATCH",
            "The retried successor classification does not match its sealed non-promoting receipt.",
            status="MISMATCH",
        )
        require(
            route.get("status") == "PASS"
            and route.get("server_identity")
            == receipt.get("native_route", {}).get("server_identity")
            and route.get("canonical_tool_namespace")
            == receipt.get("native_route", {}).get("canonical_tool_namespace")
            and route.get("tool_count")
            == receipt.get("native_route", {}).get("tool_count")
            and route.get("tool_catalog_sha256")
            == receipt.get("native_route", {}).get("tool_catalog_sha256")
            and surface.get("surface_inventory_sha256")
            == receipt.get("installed_surface", {}).get("surface_inventory_sha256")
            and sha256_bytes(canonical_json_bytes(panel))
            == receipt.get("project_panel_sha256"),
            "STATE_TRAVEL_TASK_ADVANCE_REPLAY_SURFACE_MISMATCH",
            "The retried call no longer uses the native route, installed surface, or project panel sealed by the advance receipt.",
            status="MISMATCH",
        )
        plan_transition = self.store.advance_verified_state_travel_task(
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
            "STATE_TRAVEL_TASK_ADVANCE_REPLAY_PLAN_MISMATCH",
            "The Plan was not already at the exact sealed successor state.",
            status="MISMATCH",
        )
        lineage = self._append_state_travel_task_advance_lineage(
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

    def _verify_fallback_prewarmer_task_advance(
        self,
        project_id: str,
        session_id: str,
        *,
        completed_backlog_task_id: str,
        replacement_backlog_task_id: str,
        replacement_task: TaskContract,
        fallback_prewarmer_proof: dict[str, Any] | None,
        native_route_receipt: dict[str, Any] | None,
        installed_surface_inventory: dict[str, Any] | None,
        project_panel_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Seal one candidate-free transition out of the disabled fallback row."""

        session = self.load(project_id, session_id)
        pointer = self.store.pointer(project_id)
        proof = fallback_prewarmer_proof or {}
        proof_body = {
            key: value for key, value in proof.items() if key != "receipt_sha256"
        }
        require(
            completed_backlog_task_id == _FALLBACK_PREWARM_TASK_ID
            and proof.get("schema") == "evidence-lane.codex-fallback-prewarm-proof.v1"
            and proof.get("status") == "PASS"
            and proof.get("project_id") == project_id
            and proof.get("session_id") == session_id
            and proof.get("host_session_id")
            == session.metadata.get("current_host_session_id")
            and proof.get("accepted_pv") == pointer.accepted_pv
            and proof.get("accepted_generation") == pointer.generation
            and proof.get("accepted_manifest_sha256")
            == pointer.accepted_manifest_sha256
            and proof.get("fallback_byte_frozen") is True
            and proof.get("stable_enabled") is True
            and proof.get("fallback_enabled") is False
            and proof.get("enabled_evidence_lane_plugin_count") == 1
            and proof.get("active_tunnel_count") == 0
            and proof.get("tunnel_required") is False
            and proof.get("current_task_recovery_prepared") is True
            and proof.get("restart_invoked") is False
            and proof.get("fallback_activated") is False
            and proof.get("source_mutated") is False
            and proof.get("git_mutated") is False
            and proof.get("candidate_created") is False
            and proof.get("pending_hil") is False
            and proof.get("pointer_moved") is False
            and proof.get("hil_inferred") is False
            and proof.get("receipt_sha256")
            == sha256_bytes(canonical_json_bytes(proof_body)),
            "FALLBACK_PREWARM_TASK_ADVANCE_PROOF_MISMATCH",
            "The active fallback row lacks one exact disabled-PV11 recovery proof.",
            status="MISMATCH",
            proof_error=proof.get("error"),
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
            "FALLBACK_PREWARM_TASK_ADVANCE_SESSION_MISMATCH",
            "The session is not bound to the exact active fallback prewarm row.",
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
        require(
            route.get("schema") == "evidence-lane.native-mcp-route-receipt.v1"
            and route.get("status") == "PASS"
            and route.get("server_identity") == "evidence-lane"
            and route.get("canonical_tool_namespace") == "mcp__evidence_lane__"
            and route.get("exposure_profile") == "FULL_LIFECYCLE"
            and route.get("tool_count") == NATIVE_TOOL_COUNT
            and route.get("tool_names_unique") is True
            and route.get("project_route_argument_required") is True
            and route.get("cross_project_fallback_allowed") is False
            and len(str(route.get("tool_catalog_sha256") or "")) == 64,
            "FALLBACK_PREWARM_TASK_ADVANCE_NATIVE_ROUTE_MISMATCH",
            "Fallback closeout must execute through the exact current native route.",
            status="MISMATCH",
        )
        require(
            surface.get("schema")
            == "evidence-lane.codex-installed-surface-inventory.v2"
            and str(surface.get("plugin_version") or "").split("+", 1)[0]
            == ENGINE_VERSION
            and surface.get("catalog") == expected_catalog
            and isinstance(surface.get("skills"), dict)
            and surface["skills"].get("count") == GOVERNED_SKILL_COUNT
            and surface.get("raw_paths_included") is False
            and surface.get("surface_inventory_sha256")
            == sha256_bytes(canonical_json_bytes(surface_core)),
            "FALLBACK_PREWARM_TASK_ADVANCE_INSTALLED_SURFACE_MISMATCH",
            "The running stable package does not expose the exact v2 surface.",
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
            "FALLBACK_PREWARM_TASK_ADVANCE_PROJECT_PANEL_MISMATCH",
            "The project panel no longer matches the candidate-free PV11 boundary.",
            status="MISMATCH",
        )

        backlog = self.store.backlog_status(project_id)
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
        tasks_by_id = {str(row.get("task_id")): row for row in backlog.get("tasks", [])}
        completed_plan_task = tasks_by_id.get(completed_backlog_task_id)
        replacement_plan_task = tasks_by_id.get(replacement_backlog_task_id)
        require(
            backlog.get("status") == "PASS"
            and goal.get("canonical_authority") == "PLAN_LANE"
            and goal.get("persistent_until") == "NEXT_SIX_WAY_HIL_PRESENTED"
            and numbers == list(range(numbers[0], numbers[0] + len(numbers)))
            and len(active) == 1
            and active[0].get("task_id") == completed_backlog_task_id
            and isinstance(first_queued, dict)
            and first_queued.get("task_id") == replacement_backlog_task_id
            and isinstance(completed_plan_task, dict)
            and completed_plan_task.get("status") == "ACTIVE"
            and isinstance(replacement_plan_task, dict)
            and replacement_plan_task.get("status") == "QUEUED"
            and rows[-1].get("panel_role") == "PHYSICALLY_FINAL_HIL",
            "FALLBACK_PREWARM_TASK_ADVANCE_PLAN_MISMATCH",
            "The Plan is not the contiguous one-active fallback-to-successor boundary.",
            status="MISMATCH",
        )
        prior_task = cast(dict[str, Any], session.task)
        prior_runtime_task_id = str(prior_task.get("task_id") or "").strip()
        require(
            bool(prior_runtime_task_id),
            "FALLBACK_PREWARM_TASK_ADVANCE_PRIOR_RUNTIME_TASK_REQUIRED",
            "The active fallback row has no runtime task identity.",
            status="MISMATCH",
        )
        body = {
            "schema": "evidence-lane.verified-fallback-prewarm-task-advance.v1",
            "project_id": project_id,
            "session_id": session_id,
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
            "project_panel_sha256": sha256_bytes(canonical_json_bytes(panel)),
            "fallback_prewarmer_proof": proof,
            "fallback_activated": False,
            "restart_invoked": False,
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

    def _append_fallback_prewarmer_task_advance_lineage(
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
            "FALLBACK_PREWARM_TASK_ADVANCE_LINEAGE_RECEIPT_INVALID",
            "The fallback advance cannot bind deterministic lineage events.",
            status="MISMATCH",
        )
        event_suffix = receipt_sha256[:32].lower()
        lineage = ChatLineage(self._lineage_path(project_id, session_id))
        completion_event = lineage.append(
            event_type="task.fallback_prewarmer.completed",
            visible_payload=receipt,
            occurred_at=str(
                receipt.get("fallback_prewarmer_proof", {}).get("verified_at")
                or receipt["successor_classified_at"]
            ),
            session_id=session_id,
            task_id=str(receipt["prior_runtime_task_id"]),
            run_id=(
                str(receipt.get("prior_run_id"))
                if receipt.get("prior_run_id") is not None
                else None
            ),
            event_id=f"evt_fb_advance_completed_{event_suffix}",
        )
        classification_event = lineage.append(
            event_type="task.classified",
            visible_payload=task.as_dict(),
            occurred_at=str(receipt["successor_classified_at"]),
            session_id=session_id,
            task_id=task.task_id,
            run_id=str(session.metadata["run_id"]),
            event_id=f"evt_fb_advance_classified_{event_suffix}",
        )
        return {
            "completion_event": completion_event,
            "classification_event": classification_event,
        }

    def _replay_fallback_prewarmer_task_advance(
        self,
        project_id: str,
        session_id: str,
        *,
        task: TaskContract,
        receipt: dict[str, Any],
        fallback_prewarmer_proof: dict[str, Any] | None,
        native_route_receipt: dict[str, Any] | None,
        installed_surface_inventory: dict[str, Any] | None,
        project_panel_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        receipt_body = {
            key: value for key, value in receipt.items() if key != "receipt_sha256"
        }
        proof = fallback_prewarmer_proof or {}
        route = native_route_receipt or {}
        surface = installed_surface_inventory or {}
        panel = project_panel_snapshot or {}
        require(
            receipt.get("schema")
            == "evidence-lane.verified-fallback-prewarm-task-advance.v1"
            and receipt.get("project_id") == project_id
            and receipt.get("session_id") == session_id
            and receipt.get("replacement_backlog_task_id")
            == session.metadata.get("active_backlog_task_id")
            and receipt.get("replacement_runtime_task_id") == task.task_id
            and receipt.get("receipt_sha256")
            == sha256_bytes(canonical_json_bytes(receipt_body))
            and receipt.get("fallback_prewarmer_proof", {}).get("receipt_sha256")
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
            "FALLBACK_PREWARM_TASK_ADVANCE_REPLAY_MISMATCH",
            "The retried successor no longer matches its fallback closeout receipt.",
            status="MISMATCH",
        )
        plan_transition = self.store.advance_verified_fallback_prewarmer_task(
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
            "FALLBACK_PREWARM_TASK_ADVANCE_REPLAY_PLAN_MISMATCH",
            "The Plan is not already at the exact fallback successor state.",
            status="MISMATCH",
        )
        lineage = self._append_fallback_prewarmer_task_advance_lineage(
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
        receipt = {
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
        _fallback_prewarm_proof: dict[str, Any] | None = None,
        _task_checkpoint_proof: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        pointer = self.store.pointer(project_id)
        classification_reconciliation: dict[str, Any] | None = None
        active_backlog_task_id = str(
            session.metadata.get("active_backlog_task_id") or ""
        ).strip()
        travel = session.metadata.get("state_travel")
        prior_advance_receipt = session.metadata.get("last_state_travel_task_advance")
        prior_fallback_advance_receipt = session.metadata.get(
            "last_fallback_prewarmer_task_advance"
        )
        prior_task_checkpoint_receipt = session.metadata.get(
            "last_task_checkpoint_advance"
        )
        state_travel_advance_replay_requested = (
            session.state == SessionState.TASK_CLASSIFIED
            and bool(backlog_task_id)
            and backlog_task_id == active_backlog_task_id
            and isinstance(prior_advance_receipt, dict)
            and prior_advance_receipt.get("replacement_backlog_task_id")
            == backlog_task_id
            and isinstance(travel, dict)
            and travel.get("handoff_id") == prior_advance_receipt.get("handoff_id")
        )
        state_travel_advance_requested = (
            session.state == SessionState.TASK_CLASSIFIED
            and bool(backlog_task_id)
            and bool(active_backlog_task_id)
            and backlog_task_id != active_backlog_task_id
            and isinstance(travel, dict)
            and travel.get("status") == "VERIFIED_RESUME_READY"
            and not isinstance(prior_advance_receipt, dict)
            and active_backlog_task_id != _FALLBACK_PREWARM_TASK_ID
        )
        fallback_advance_replay_requested = (
            session.state == SessionState.TASK_CLASSIFIED
            and bool(backlog_task_id)
            and backlog_task_id == active_backlog_task_id
            and isinstance(prior_fallback_advance_receipt, dict)
            and prior_fallback_advance_receipt.get("replacement_backlog_task_id")
            == backlog_task_id
        )
        fallback_advance_requested = (
            session.state == SessionState.TASK_CLASSIFIED
            and active_backlog_task_id == _FALLBACK_PREWARM_TASK_ID
            and bool(backlog_task_id)
            and backlog_task_id != active_backlog_task_id
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
            and active_backlog_task_id != _FALLBACK_PREWARM_TASK_ID
            and isinstance(_task_checkpoint_proof, dict)
        )
        deterministic_task_id: str | None = None
        if state_travel_advance_requested or state_travel_advance_replay_requested:
            deterministic_task_id = (
                "task_st_"
                + sha256_bytes(
                    canonical_json_bytes(
                        {
                            "project_id": project_id,
                            "session_id": session_id,
                            "handoff_id": cast(dict[str, Any], travel).get(
                                "handoff_id"
                            ),
                            "replacement_backlog_task_id": backlog_task_id,
                        }
                    )
                )[:26].lower()
            )
        elif fallback_advance_requested or fallback_advance_replay_requested:
            deterministic_task_id = (
                "task_fb_"
                + sha256_bytes(
                    canonical_json_bytes(
                        {
                            "project_id": project_id,
                            "session_id": session_id,
                            "fallback_prewarmer_proof_sha256": (
                                (_fallback_prewarm_proof or {}).get("receipt_sha256")
                            ),
                            "replacement_backlog_task_id": backlog_task_id,
                        }
                    )
                )[:26].lower()
            )
        elif task_checkpoint_advance_requested or task_checkpoint_replay_requested:
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
        state_travel_task_advance: dict[str, Any] | None = None
        fallback_prewarmer_task_advance: dict[str, Any] | None = None
        task_checkpoint_advance: dict[str, Any] | None = None
        if state_travel_advance_replay_requested:
            state_travel_task_advance = self._replay_state_travel_task_advance(
                project_id,
                session_id,
                task=task,
                receipt=cast(dict[str, Any], prior_advance_receipt),
                native_route_receipt=_native_route_receipt,
                installed_surface_inventory=_installed_surface_inventory,
                project_panel_snapshot=_project_panel_snapshot,
            )
            return {
                "status": "PASS",
                "session": session.as_dict(),
                "task": task.as_dict(),
                "classification_reconciliation": None,
                "state_travel_task_advance": state_travel_task_advance,
                "classification_binding": session.metadata.get(
                    "task_classification_binding"
                ),
            }
        if fallback_advance_replay_requested:
            fallback_prewarmer_task_advance = (
                self._replay_fallback_prewarmer_task_advance(
                    project_id,
                    session_id,
                    task=task,
                    receipt=cast(dict[str, Any], prior_fallback_advance_receipt),
                    fallback_prewarmer_proof=_fallback_prewarm_proof,
                    native_route_receipt=_native_route_receipt,
                    installed_surface_inventory=_installed_surface_inventory,
                    project_panel_snapshot=_project_panel_snapshot,
                )
            )
            return {
                "status": "PASS",
                "session": session.as_dict(),
                "task": task.as_dict(),
                "classification_reconciliation": None,
                "state_travel_task_advance": None,
                "fallback_prewarmer_task_advance": (fallback_prewarmer_task_advance),
                "classification_binding": session.metadata.get(
                    "task_classification_binding"
                ),
            }
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
                "state_travel_task_advance": None,
                "fallback_prewarmer_task_advance": None,
                "task_checkpoint_advance": task_checkpoint_advance,
                "classification_binding": session.metadata.get(
                    "task_classification_binding"
                ),
            }
        if state_travel_advance_requested:
            state_travel_task_advance = self._verify_state_travel_task_advance(
                project_id,
                session_id,
                completed_backlog_task_id=active_backlog_task_id,
                replacement_backlog_task_id=cast(str, backlog_task_id),
                replacement_task=task,
                native_route_receipt=_native_route_receipt,
                installed_surface_inventory=_installed_surface_inventory,
                project_panel_snapshot=_project_panel_snapshot,
            )
        if fallback_advance_requested:
            fallback_prewarmer_task_advance = (
                self._verify_fallback_prewarmer_task_advance(
                    project_id,
                    session_id,
                    completed_backlog_task_id=active_backlog_task_id,
                    replacement_backlog_task_id=cast(str, backlog_task_id),
                    replacement_task=task,
                    fallback_prewarmer_proof=_fallback_prewarm_proof,
                    native_route_receipt=_native_route_receipt,
                    installed_surface_inventory=_installed_surface_inventory,
                    project_panel_snapshot=_project_panel_snapshot,
                )
            )
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
            and not state_travel_advance_requested
            and not fallback_advance_requested
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
            state_travel_task_advance is not None
            or fallback_prewarmer_task_advance is not None
            or task_checkpoint_advance is not None
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
            session.task is None
            or state_travel_task_advance is not None
            or fallback_prewarmer_task_advance is not None
            or task_checkpoint_advance is not None,
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
        prior_state_travel_run: dict[str, Any] | None = None
        prior_fallback_prewarmer_run: dict[str, Any] | None = None
        prior_task_checkpoint_run: dict[str, Any] | None = None
        if state_travel_task_advance is not None:
            prior_state_travel_run = {
                "state": session.state.value,
                "task": state_travel_task_advance["prior_task"],
                "run_id": session.metadata.get("run_id"),
                "task_mode_binding": session.metadata.get("task_mode_binding"),
                "active_backlog_task_id": active_backlog_task_id,
                "active_backlog_task_status": session.metadata.get(
                    "active_backlog_task_status"
                ),
            }
            session.metadata.pop("task_mode_binding", None)
        if fallback_prewarmer_task_advance is not None:
            prior_fallback_prewarmer_run = {
                "state": session.state.value,
                "task": fallback_prewarmer_task_advance["prior_task"],
                "run_id": session.metadata.get("run_id"),
                "task_mode_binding": session.metadata.get("task_mode_binding"),
                "active_backlog_task_id": active_backlog_task_id,
                "active_backlog_task_status": session.metadata.get(
                    "active_backlog_task_status"
                ),
            }
            session.metadata.pop("task_mode_binding", None)
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
            or state_travel_task_advance is not None
            or fallback_prewarmer_task_advance is not None
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
            if state_travel_task_advance is not None:
                receipt = cast(dict[str, Any], state_travel_task_advance["receipt"])
                claimed_advance = self.store.advance_verified_state_travel_task(
                    project_id,
                    completed_backlog_task_id=active_backlog_task_id,
                    replacement_backlog_task_id=backlog_task_id,
                    session_id=session_id,
                    prior_runtime_task_id=str(receipt["prior_runtime_task_id"]),
                    replacement_contract=task.as_dict(),
                    completion_receipt=receipt,
                )
                state_travel_task_advance["plan_transition"] = claimed_advance
                session.metadata["active_backlog_task_id"] = backlog_task_id
                session.metadata["active_backlog_task_status"] = "ACTIVE"
                session.metadata.setdefault("completed_runs", []).append(
                    {
                        **cast(dict[str, Any], prior_state_travel_run),
                        "completion_disposition": (
                            "VERIFIED_STATE_TRAVEL_HANDOFF_COMPLETED_WITHOUT_CANDIDATE"
                        ),
                        "state_travel_task_advance_receipt_sha256": receipt[
                            "receipt_sha256"
                        ],
                    }
                )
                session.metadata.setdefault("state_travel_task_advances", []).append(
                    receipt
                )
                session.metadata["last_state_travel_task_advance"] = receipt
            elif fallback_prewarmer_task_advance is not None:
                receipt = cast(
                    dict[str, Any], fallback_prewarmer_task_advance["receipt"]
                )
                claimed_advance = self.store.advance_verified_fallback_prewarmer_task(
                    project_id,
                    completed_backlog_task_id=active_backlog_task_id,
                    replacement_backlog_task_id=backlog_task_id,
                    session_id=session_id,
                    prior_runtime_task_id=str(receipt["prior_runtime_task_id"]),
                    replacement_contract=task.as_dict(),
                    completion_receipt=receipt,
                )
                fallback_prewarmer_task_advance["plan_transition"] = claimed_advance
                session.metadata["active_backlog_task_id"] = backlog_task_id
                session.metadata["active_backlog_task_status"] = "ACTIVE"
                session.metadata.setdefault("completed_runs", []).append(
                    {
                        **cast(dict[str, Any], prior_fallback_prewarmer_run),
                        "completion_disposition": (
                            "VERIFIED_FALLBACK_PREWARM_COMPLETED_WITHOUT_CANDIDATE"
                        ),
                        "fallback_prewarmer_task_advance_receipt_sha256": receipt[
                            "receipt_sha256"
                        ],
                    }
                )
                session.metadata.setdefault(
                    "fallback_prewarmer_task_advances", []
                ).append(receipt)
                session.metadata["last_fallback_prewarmer_task_advance"] = receipt
            elif task_checkpoint_advance is not None:
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
        if state_travel_task_advance is not None:
            require(
                target_state == SessionState.TASK_CLASSIFIED,
                "STATE_TRAVEL_TASK_ADVANCE_CLIENT_AUTHORITY_MISMATCH",
                "Verified handoff closeout requires direct client source authority.",
                status="MISMATCH",
            )
            session.state = transition(
                session.state,
                LifecycleEvent.ADVANCE_VERIFIED_STATE_TRAVEL_TASK,
                target_state,
            )
            session.metadata["run_id"] = (
                "run_st_"
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
        elif fallback_prewarmer_task_advance is not None:
            require(
                target_state == SessionState.TASK_CLASSIFIED,
                "FALLBACK_PREWARM_TASK_ADVANCE_CLIENT_AUTHORITY_MISMATCH",
                "Fallback closeout requires direct client source authority.",
                status="MISMATCH",
            )
            session.state = transition(
                session.state,
                LifecycleEvent.ADVANCE_VERIFIED_FALLBACK_PREWARM_TASK,
                target_state,
            )
            session.metadata["run_id"] = (
                "run_fb_"
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
        elif task_checkpoint_advance is not None:
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
        if state_travel_task_advance is not None:
            receipt = cast(dict[str, Any], state_travel_task_advance["receipt"])
            session.metadata["task_source_basis"] = {
                "kind": "VERIFIED_STATE_TRAVEL_HANDOFF_ADVANCE",
                "accepted_pv": pointer.accepted_pv,
                "pointer_generation": pointer.generation,
                "completed_backlog_task_id": active_backlog_task_id,
                "replacement_backlog_task_id": backlog_task_id,
                "state_travel_task_advance_receipt_sha256": receipt["receipt_sha256"],
                "current_source_identity_sha256": receipt[
                    "current_source_identity_sha256"
                ],
            }
            session.metadata["source_state"] = "VERIFIED_STATE_TRAVEL_HANDOFF_ADVANCE"
            session.metadata["accepted_pv_query_scope"] = "ENTRY_STATE_ONLY"
        elif fallback_prewarmer_task_advance is not None:
            receipt = cast(dict[str, Any], fallback_prewarmer_task_advance["receipt"])
            session.metadata["task_source_basis"] = {
                "kind": "VERIFIED_FALLBACK_PREWARM_ADVANCE",
                "accepted_pv": pointer.accepted_pv,
                "pointer_generation": pointer.generation,
                "completed_backlog_task_id": active_backlog_task_id,
                "replacement_backlog_task_id": backlog_task_id,
                "fallback_prewarmer_task_advance_receipt_sha256": receipt[
                    "receipt_sha256"
                ],
                "fallback_prewarmer_proof_sha256": receipt["fallback_prewarmer_proof"][
                    "receipt_sha256"
                ],
            }
            session.metadata["source_state"] = "VERIFIED_FALLBACK_PREWARM_ADVANCE"
            session.metadata["accepted_pv_query_scope"] = "ENTRY_STATE_ONLY"
        elif task_checkpoint_advance is not None:
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
        state_travel_lineage: dict[str, Any] | None = None
        fallback_prewarmer_lineage: dict[str, Any] | None = None
        task_checkpoint_lineage: dict[str, Any] | None = None
        if state_travel_task_advance is not None:
            receipt = cast(dict[str, Any], state_travel_task_advance["receipt"])
            state_travel_lineage = self._append_state_travel_task_advance_lineage(
                project_id,
                session_id,
                session=session,
                task=task,
                receipt=receipt,
            )
        if fallback_prewarmer_task_advance is not None:
            receipt = cast(dict[str, Any], fallback_prewarmer_task_advance["receipt"])
            fallback_prewarmer_lineage = (
                self._append_fallback_prewarmer_task_advance_lineage(
                    project_id,
                    session_id,
                    session=session,
                    task=task,
                    receipt=receipt,
                )
            )
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
        if (
            state_travel_task_advance is None
            and fallback_prewarmer_task_advance is None
            and task_checkpoint_advance is None
        ):
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
            "state_travel_task_advance": state_travel_task_advance,
            "state_travel_lineage": state_travel_lineage,
            "fallback_prewarmer_task_advance": fallback_prewarmer_task_advance,
            "fallback_prewarmer_lineage": fallback_prewarmer_lineage,
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
            backlog_done = self.store.record_backlog_done(
                project_id,
                backlog_task_id=cast(str, backlog_task_id),
                session_id=session_id,
                candidate_id=result["candidate_id"],
            )
            session.metadata["active_backlog_task_status"] = "DONE"
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
            )
            session.metadata.pop("active_backlog_task_id", None)
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
            Path(__file__).resolve().parents[2] / ".codex-plugin" / "plugin.json"
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
        plugin_root = Path(__file__).resolve().parents[2]
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
    def _direct_entry_recovery_authority(
        session: SessionRecord,
        *,
        project_id: str,
        destination: dict[str, Any],
        exact_binding: dict[str, Any],
        plan: dict[str, Any],
        runtime_instance_attestation: dict[str, Any],
        rebound_at: str,
    ) -> dict[str, Any]:
        """Bind checkpoint recovery to the server-attested direct destination."""

        destination_task_id = str(destination.get("task_id") or "").strip()
        active_plan_task_id = str(plan.get("active_task_id") or "").strip()
        runtime_task = cast(dict[str, Any], session.task or {})
        runtime_task_id = str(runtime_task.get("task_id") or "").strip()
        require(
            bool(destination_task_id)
            and bool(active_plan_task_id)
            and bool(runtime_task_id)
            and session.metadata.get("current_host_session_id")
            == destination_task_id
            and session.metadata.get("active_backlog_task_id")
            == active_plan_task_id
            and session.metadata.get("active_backlog_task_status") == "ACTIVE"
            and runtime_instance_attestation.get("status") == "PASS"
            and runtime_instance_attestation.get("caller_supplied") is False
            and runtime_instance_attestation.get("process_id_exposed") is False
            and len(
                str(runtime_instance_attestation.get("receipt_sha256") or "")
            )
            == 64,
            "DIRECT_STATE_TRAVEL_RECOVERY_AUTHORITY_MISMATCH",
            "Direct entry cannot bind checkpoint recovery without the exact destination, active Plan row, runtime task, and server attestation.",
            status="MISMATCH",
            writes_performed=False,
        )
        history = session.metadata.setdefault("active_contract_rebinds", [])
        require(
            isinstance(history, list),
            "DIRECT_STATE_TRAVEL_RECOVERY_HISTORY_INVALID",
            "The session recovery-authority history must remain append-only data.",
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
            "recovery_binding_contract": {
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
            "pending_hil": False,
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
            "DIRECT_STATE_TRAVEL_RECOVERY_AUTHORITY_CONFLICT",
            "The direct-entry recovery identity already contains different sealed bytes.",
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
        current_host_session_id = str(
            session.metadata.get("current_host_session_id") or ""
        ).strip().lower()
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
                    "runtime_instance_attestation_mode": (
                        "SERVER_DERIVED_ATTESTATION"
                    ),
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
        require(
            session.state == SessionState.TASK_CLASSIFIED
            and session.candidate_id is None
            and not bool(session.metadata.get("pending_hil"))
            and not isinstance(session.metadata.get("pending_task"), dict),
            "DIRECT_STATE_TRAVEL_UNACCEPTED_STATE_PRESENT",
            "Direct destination entry requires no candidate, pending HIL, or HIL follow-up.",
            status="BLOCKED",
            writes_performed=False,
        )
        stale_sealed = session.metadata.get("state_travel")
        if isinstance(stale_sealed, dict) and stale_sealed.get("status") == "PREPARED":
            require(
                stale_sealed.get("origin_host_session_id") != source_task["task_id"],
                "DIRECT_STATE_TRAVEL_ELIGIBLE_SEALED_HANDOFF_PRESENT",
                "An eligible fresh sealed handoff exists; the direct route cannot bypass it.",
                status="BLOCKED",
                writes_performed=False,
            )

        entry_validation = self.accepted_entry_validation(
            project_id,
            str(pointer.accepted_pv),
        )
        now = utc_now()
        previous_continuity = cast(
            dict[str, Any], session.metadata.get("runtime_continuity") or {}
        )
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
            accepted_manifest_sha256=entry_validation["manifest_sha256"],
            accepted_package_sha256=entry_validation["package_sha256"],
            accepted_promotable_under_current_rules=entry_validation["promotable"],
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

        calling_task_recovery_authority = self._direct_entry_recovery_authority(
            session,
            project_id=project_id,
            destination=destination,
            exact_binding=exact,
            plan=plan,
            runtime_instance_attestation=runtime_instance_attestation,
            rebound_at=now,
        )

        destination_orchestration = build_direct_destination_orchestration(exact)

        receipt_body = {
            "schema": "evidence-lane.direct-forced-same-worktree-entry-receipt.v1",
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
            "calling_task_recovery_authority": {
                "schema": calling_task_recovery_authority["schema"],
                "status": calling_task_recovery_authority["status"],
                "receipt_sha256": calling_task_recovery_authority["receipt_sha256"],
                "active_plan_task_id": calling_task_recovery_authority[
                    "active_plan_task_id"
                ],
                "runtime_task_id": calling_task_recovery_authority[
                    "runtime_task_id"
                ],
                "host_task_id": calling_task_recovery_authority["host_task_id"],
                "authority_route": calling_task_recovery_authority[
                    "authority_route"
                ],
                "runtime_instance_attestation_receipt_sha256": (
                    calling_task_recovery_authority["direct_entry_authority"][
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

    def _state_travel_resume_contract(
        self,
        project_id: str,
        session: SessionRecord,
        supplied: dict[str, Any] | None,
        *,
        travel_mode: str,
        source_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw = supplied or {}
        task_list_source = "EXPLICIT_STATE_TRAVEL_INPUT"
        raw_task_list = raw.get("task_list")
        canonical_plan_task_list: list[dict[str, Any]] = []
        backlog = self.store.backlog_status(project_id)
        canonical_task_rows = self._state_travel_canonical_task_rows(backlog)
        if backlog["active"]:
            canonical_plan_task_list = normalize_task_list(canonical_task_rows)
        if raw_task_list is None:
            if canonical_plan_task_list:
                raw_task_list = canonical_task_rows
                task_list_source = "ACTIVE_PLAN_LANE_DERIVED"
            elif session.task is not None:
                raw_task_list = [
                    {
                        "task_id": session.task.get("task_id", "ACTIVE_SESSION_TASK"),
                        "step": session.task.get("requested_outcome"),
                        "status": "IN_PROGRESS",
                    }
                ]
                task_list_source = "ACTIVE_SESSION_TASK_DERIVED"
            elif isinstance(session.metadata.get("pending_task"), dict):
                pending = cast(dict[str, Any], session.metadata["pending_task"])
                raw_task_list = [
                    {
                        "task_id": "PENDING_HIL_FOLLOW_UP",
                        "step": pending.get("requested_outcome"),
                        "status": "IN_PROGRESS",
                    }
                ]
                task_list_source = "PENDING_TASK_DERIVED"
            elif session.candidate_id:
                raw_task_list = [
                    {
                        "task_id": "PRESENT_PENDING_HIL",
                        "step": "Present the preserved pending candidate at its six-way HIL",
                        "status": "IN_PROGRESS",
                    }
                ]
                task_list_source = "PENDING_CANDIDATE_DERIVED"
            else:
                raw_task_list = []
                task_list_source = "NO_ACTIVE_PLAN"
        task_list = normalize_task_list(raw_task_list)
        goal_projection = cast(dict[str, Any], backlog["goal_projection"])
        if canonical_plan_task_list and raw.get("task_list") is not None:
            require(
                task_list == canonical_plan_task_list,
                "STATE_TRAVEL_EXPLICIT_TASK_LIST_PLAN_MISMATCH",
                "The supplied State Travel task list does not exactly match the "
                "active canonical Plan Lane, including every steer Delta.",
                status="MISMATCH",
                supplied_task_list_sha256=sha256_bytes(canonical_json_bytes(task_list)),
                canonical_task_list_sha256=sha256_bytes(
                    canonical_json_bytes(canonical_plan_task_list)
                ),
            )
        if travel_mode == "UNFINISHED_VERIFIED_WORK":
            require(
                bool(task_list),
                "STATE_TRAVEL_UNFINISHED_TASK_LIST_REQUIRED",
                "Unfinished-work State Travel requires the active Plan Lane/task list; "
                "historical accepted Delta rows are not a substitute.",
                status="BLOCKED",
                task_list_source=task_list_source,
            )
        active_rows = [row for row in task_list if row["status"] == "IN_PROGRESS"]
        supplied_resume_step = raw.get("resume_step")
        if supplied_resume_step is None:
            resume_step = (
                active_rows[0]["number"]
                if active_rows
                else next(
                    (row["number"] for row in task_list if row["status"] == "PENDING"),
                    None,
                )
            )
        elif isinstance(supplied_resume_step, int):
            resume_step = supplied_resume_step
        else:
            supplied_task_id = str(supplied_resume_step)
            resume_step = next(
                (
                    row["number"]
                    for row in task_list
                    if row["task_id"] == supplied_task_id
                ),
                None,
            )
        resume_row = next(
            (
                row
                for row in task_list
                if isinstance(resume_step, int) and row["number"] == resume_step
            ),
            None,
        )
        if travel_mode == "UNFINISHED_VERIFIED_WORK":
            require(
                isinstance(resume_step, int)
                and isinstance(resume_row, dict)
                and resume_row["status"] != "COMPLETED",
                "STATE_TRAVEL_RESUME_STEP_INVALID",
                "The exact resume step must identify one unfinished task-panel row.",
                status="BLOCKED",
                resume_step=resume_step,
            )
            if active_rows:
                require(
                    resume_step == active_rows[0]["number"],
                    "STATE_TRAVEL_RESUME_STEP_ACTIVE_MISMATCH",
                    "The resume step must match the one in-progress task-panel row.",
                    status="MISMATCH",
                    resume_step=resume_step,
                    active_step=active_rows[0]["number"],
                )
        canonical_plan_deltas = additive_deltas_from_task_list(
            canonical_plan_task_list or task_list
        )
        supplied_additive_deltas = raw.get("additive_deltas")
        additive_deltas = (
            normalize_additive_deltas(supplied_additive_deltas)
            if supplied_additive_deltas is not None
            else canonical_plan_deltas
        )
        supplied_by_id = {row["delta_id"]: row for row in additive_deltas}
        missing_or_changed_plan_deltas = [
            row["delta_id"]
            for row in canonical_plan_deltas
            if supplied_by_id.get(row["delta_id"]) != row
        ]
        require(
            not missing_or_changed_plan_deltas,
            "STATE_TRAVEL_PLAN_DELTA_SEAL_INCOMPLETE",
            "State Travel must seal every canonical Plan Lane steer Delta exactly.",
            status="MISMATCH",
            delta_ids=missing_or_changed_plan_deltas,
        )
        valid_task_numbers = {row["number"] for row in task_list}
        invalid_delta_links = [
            row["delta_id"]
            for row in additive_deltas
            if row["linked_step"] is not None
            and row["linked_step"] not in valid_task_numbers
        ]
        require(
            not invalid_delta_links,
            "STATE_TRAVEL_DELTA_LINK_OUT_OF_RANGE",
            "A State Travel Delta links outside the persistent task list.",
            status="MISMATCH",
            delta_ids=invalid_delta_links,
        )
        supplied_profile = raw.get("execution_profile")
        if supplied_profile is not None:
            execution_profile = execution_profile_from_context(
                {"execution_profile": supplied_profile}
            )
        else:
            execution_profile = execution_profile_from_context(
                {"execution_profile": session.metadata.get("execution_profile", {})}
            )
        if travel_mode == "UNFINISHED_VERIFIED_WORK":
            require_unfinished_execution_profile(
                execution_profile,
                host_kind=session.host.value,
            )
        default_prompt = (
            f"Resume Evidence Lane project {project_id} at step {resume_step}: "
            f"{resume_row['step']} After the fresh destination is created, bound, "
            "and resumed exactly once, re-project the exact complete task panel, "
            "wait for explicit host Plan acceptance, then run Evidence Plan and "
            "start the carried Goal automatically. Preserve every status, description, order, "
            "and additive Delta, and keep it visible as sole writer through every "
            "pause and HIL until the physically final six-way HIL is decided and "
            "all decision-dependent work is complete."
            if resume_row
            else (
                f"Open Evidence Lane project {project_id} at its exact accepted "
                "pointer and wait for the next bounded user command."
            )
        )
        supplied_prompt = raw.get("suggested_next_prompt")
        suggested_next_prompt = (
            supplied_prompt
            if isinstance(supplied_prompt, str) and supplied_prompt.strip()
            else default_prompt
        )
        task_list_sha256 = sha256_bytes(canonical_json_bytes(task_list))
        panel_reactivation = {
            "schema": "evidence-lane.persistent-panel-reactivation.v1",
            "required": bool(task_list),
            "triggers": [
                "TOKEN_DRIVEN_CONTINUATION",
                "STALLED_GOAL",
                "CONTEXT_COMPACTION",
                "BROWSER_RESTART",
                "CODEX_RESTART",
                "SESSION_CONTINUATION",
                "SESSION_RESUME",
                "STATE_TRAVEL_DESTINATION_ENTRY",
                "APP_RENDERER_RELOAD",
                "HOST_REACT_ROOT_RERENDER",
                "THREAD_HYDRATION_OVERFLOW",
                "COLLABORATION_OVERLAY_CONFLICT",
                "TASK_PANEL_LOSS",
                "CHANGES_SURFACE_LOSS",
            ],
            "first_required_action": (
                "REPROJECT_EXACT_COMPLETE_TASK_LIST"
                if task_list
                else "NO_TASK_PANEL_PRESENT"
            ),
            "must_precede": [
                "SOURCE_INSPECTION",
                "SOURCE_MUTATION",
                "TESTING",
                "GIT_ACTIVITY",
                "SUBSEQUENT_LIFECYCLE_CALL",
            ],
            "state_travel_destination_first_native_lifecycle_action": (
                "PV_STATE_TRAVEL_RESUME_EXACTLY_ONCE"
            ),
            "state_travel_destination_first_host_action_after_resume": (
                "REPROJECT_EXACT_COMPLETE_TASK_LIST"
            ),
            "host_plan_acceptance_required_before_evidence_plan": bool(task_list),
            "host_plan_acceptance_is_evidence_lane_hil": False,
            "goal_or_source_work_before_host_plan_acceptance": False,
            "task_list_sha256": task_list_sha256,
            "visible_row_start": task_list[0]["number"] if task_list else None,
            "visible_row_end": task_list[-1]["number"] if task_list else None,
            "visible_row_numbering": ("DYNAMIC_ASCENDING_CURRENT_EXECUTION_PROJECTION"),
            "stable_identity_field": "task_id",
            "renumber_after_insert_or_non_executable_transition": True,
            "active_row": active_rows[0]["number"] if active_rows else None,
            "non_empty_task_list_requires_exactly_one_in_progress": True,
            "preserve_order_and_row_count": True,
            "preserve_completed_and_pending_descriptions_unabridged": True,
            "preserve_row_task_name_class_group_batch_dependencies_git_stage": True,
            "visible_label_contract": goal_projection.get("visible_label_contract"),
            "visible_through_pause_and_hil": True,
            "native_host_surfaces": [
                "CODEX_RIGHT_SIDE_PLAN",
                "CODEX_RIGHT_SIDE_CHANGES",
            ],
            "native_plan_activation_action": "update_plan",
            "changes_surface_binding": "EXACT_TASK_UUID_AND_WORKTREE",
            "surface_drop_before_goal_completion": (
                "HOST_CONTINUITY_FAILURE_THEN_REHYDRATE_BEFORE_WORK"
            ),
            "canonical_rehydration_source": ("PLAN_LANE_BACKLOG_NOT_THREAD_HISTORY"),
            "full_thread_history_hydration_allowed": False,
            "collaboration_overlay_hydration_allowed_during_recovery": False,
            "recovery_concurrency": "ONE_ACTIVE_TASK_ZERO_SUBAGENTS",
            "renderer_reset_effect": (
                "FAIL_CLOSED_THEN_REPROJECT_EXACTLY_ONCE_PER_EVENT"
            ),
            "host_owned_surface_guarantee_claimed": False,
            "goal_completion_authority": "HUMAN_ONLY",
            "drop_allowed_when": (
                "HUMAN_MARKS_GOAL_COMPLETE_OR_EXPLICIT_TASK_STATE_TRAVEL_HANDOFF_PASSES"
            ),
        }
        execution_writer_boundary = {
            "schema": "evidence-lane.execution-writer-boundary.v1",
            "project_policy": "ONE_GOVERNED_PROJECT",
            "writer_policy": "ONE_LIVE_WRITER",
            "execution_order": "LINEAR",
            "verification_order": "EVIDENCE_FIRST",
            "execution_profile": execution_profile,
            "execution_profile_change_authority": "EXPLICIT_USER_CHANGE_ONLY",
            "entry_recovery_agents": ("READ_ONLY_ONLY_AT_GENUINE_STATE_TRAVEL_ENTRY"),
            "later_subagents": "EXPLICIT_USER_COMMAND_ONLY",
            "alternate_checkout_writer": ("FORBIDDEN_UNLESS_EXPLICIT_USER_CHANGE"),
            "background_mutation": "FORBIDDEN_UNLESS_EXPLICIT_USER_CHANGE",
            "browser_profile": (
                "ONE_USER_SELECTED_PROFILE_ONLY_UNLESS_EXPLICIT_USER_CHANGE"
            ),
        }
        goal_continuity = {
            "schema": "evidence-lane.goal-continuity.v1",
            "project_id": project_id,
            "session_id": session.session_id,
            "plan_authority": "SAME_CANONICAL_PLAN_LANE",
            "source_boundary": "SAME_ACTIVE_SOURCE_BOUNDARY",
            "writer_session": "SAME_SINGLE_WRITER_SESSION",
            "task_list_sha256": task_list_sha256,
            "active_row": active_rows[0]["number"] if active_rows else None,
            "pause_triggers": [
                "UI_CRASH",
                "TOKEN_WAIT",
                "REQUIRED_USER_INPUT",
                "HIL_WAIT",
            ],
            "pause_effect": "PAUSE_DEPENDENT_WORK_ONLY",
            "goal_completion_effect_while_waiting": "FORBIDDEN",
            "usage_reporting_task_status_effect": "NONE",
            "reconstruction_requires": [
                "ALL_COMPLETED_BUT_STILL_GOVERNING_ROWS",
                "EXACTLY_ONE_ACTIVE_ROW_WHEN_PANEL_PRESENT",
                "ALL_PENDING_ROWS",
            ],
            "completed_governing_rows_may_be_omitted": False,
            "goal_completion_allowed_when": (
                "HUMAN_EXPLICIT_GOAL_COMPLETION_DISPOSITION"
            ),
            "goal_completion_authority": "HUMAN_ONLY",
            "hil_may_complete_goal": False,
            "goal_completion_modes": [
                "COMPLETE_THIS_TASK_AND_STATE_TRAVEL",
                "COMPLETE_FULLY",
            ],
        }
        codex_host = session.host.value.startswith("CODEX")
        source_task_id = str(
            session.metadata.get("current_host_session_id") or ""
        ).strip()
        if codex_host and travel_mode == "UNFINISHED_VERIFIED_WORK":
            require(
                bool(source_task_id),
                "STATE_TRAVEL_SOURCE_TASK_ID_REQUIRED",
                "Unfinished Codex State Travel requires the exact source task identity.",
                status="BLOCKED",
            )
        source_task_deep_link = self._state_travel_task_deep_link(
            session.host.value,
            source_task_id,
        )
        plugin_build = (
            self._state_travel_plugin_build_identity() if codex_host else None
        )
        pointer = self.store.pointer(project_id)
        source_task_binding_body = {
            "schema": "evidence-lane.state-travel-source-task-binding.v1",
            "project_id": project_id,
            "evidence_session_id": session.session_id,
            "source_task_id": source_task_id or None,
            "source_task_deep_link": source_task_deep_link,
            "source_task_deep_link_sha256": (
                sha256_bytes(source_task_deep_link.encode("utf-8"))
                if source_task_deep_link
                else None
            ),
            "accepted_pointer": {
                "accepted_pv": pointer.accepted_pv,
                "generation": pointer.generation,
                "manifest_sha256": pointer.accepted_manifest_sha256,
            },
            "active_row": active_rows[0]["number"] if active_rows else None,
            "active_task_id": (active_rows[0]["task_id"] if active_rows else None),
            "source_identity_sha256": (
                source_snapshot.get("identity_sha256")
                if isinstance(source_snapshot, dict)
                else None
            ),
            "source_worktree_sha256": (
                source_snapshot.get("worktree_sha256")
                if isinstance(source_snapshot, dict)
                else None
            ),
            "plugin_build": plugin_build,
            "execution_profile": execution_profile,
            "destination_task_id": "BOUND_AT_DESTINATION_ENTRY",
            "destination_task_deep_link": "BOUND_AT_DESTINATION_ENTRY",
            "identity_basis": (
                "EXACT_SOURCE_AND_DESTINATION_TASK_IDS_PLUS_DEEP_LINKS_"
                "PROJECT_SESSION_POINTER_SOURCE_PLUGIN_AND_PLAN"
            ),
            "task_title_used_as_identity": False,
            "cwd_used_as_identity": False,
        }
        source_task_binding = {
            **source_task_binding_body,
            "binding_sha256": sha256_bytes(
                canonical_json_bytes(source_task_binding_body)
            ),
        }
        physically_final_hil = next(
            (
                row
                for row in reversed(task_list)
                if row.get("panel_role") == "PHYSICALLY_FINAL_HIL"
            ),
            None,
        )
        destination_orchestration = {
            "schema": ("evidence-lane.state-travel-destination-orchestration.v2"),
            "enabled": bool(
                codex_host and task_list and travel_mode == "UNFINISHED_VERIFIED_WORK"
            ),
            "trigger": "STATE_TRAVEL_DESTINATION_ENTRY",
            "execution": "AUTOMATIC_LINEAR_HOST_ORCHESTRATION",
            "canonical_task_title_increment_law": (
                "SOURCE_TASK_X_TO_FRESH_DESTINATION_TASK_X_PLUS_1"
            ),
            "destination_creation_action": "CONTINUE_IN_NEW_CHAT",
            "destination_creation_programmatic_when_supported": True,
            "destination_creation_user_click_required": False,
            "destination_creation_exactly_once": True,
            "destination_creation_capability_detection_required": True,
            "destination_creation_capability_unavailable_behavior": (
                "FAIL_CLOSED_WITHOUT_RESUME_OR_GOAL"
            ),
            "host_continuity_failure_code": ("STATE_TRAVEL_HOST_CONTINUITY_FAILURE"),
            "app_restart_or_renderer_reload_allowed": False,
            "full_thread_history_hydration_allowed": False,
            "collaboration_overlay_hydration_allowed": False,
            "destination_thread_hydration_mode": ("BOUNDED_HANDOFF_ENVELOPE_ONLY"),
            "host_plan_projection_source": ("CANONICAL_PLAN_LANE_NOT_THREAD_HISTORY"),
            "unexpected_task_or_agent_activation_allowed": False,
            "host_continuity_failure_retry_allowed": False,
            "host_continuity_failure_handoff_consumption_allowed": False,
            "host_continuity_required_proof": {
                "schema": "evidence-lane.state-travel-host-continuity.v1",
                "same_host_process_instance": True,
                "exact_initial_shell_source_and_destination_task_ids": True,
                "exact_host_creation_result_task_id_and_deep_link": True,
                "zero_app_restart_renderer_reload_ui_freeze_events": True,
                "zero_unexpected_navigation_task_or_agent_activations": True,
                "zero_unbounded_thread_or_collaboration_overlay_hydrations": True,
                "bounded_handoff_envelope_only": True,
                "canonical_plan_not_thread_history": True,
                "one_live_canonical_destination_title_identity": True,
                "title_or_cwd_identity_allowed": False,
            },
            "source_task_binding": source_task_binding,
            "destination_binding_required_fields": [
                "SOURCE_TASK_ID_AND_DEEP_LINK",
                "DESTINATION_TASK_ID_AND_DEEP_LINK",
                "PROJECT_AND_EVIDENCE_SESSION",
                "ACCEPTED_POINTER",
                "ACTIVE_ROW",
                "HOST_SESSION",
                "PLUGIN_BUILD",
                "SOURCE_IDENTITY_AND_DIRTY_UNTRACKED_WORKTREE",
                "EXECUTION_PROFILE",
                "HOST_PROCESS_AND_INITIAL_SHELL_CONTINUITY",
            ],
            "title_or_cwd_only_binding_allowed": False,
            "manual_plan_mode_command_required": False,
            "manual_evi_plan_command_required": False,
            "manual_goal_prompt_paste_required": False,
            "host_mode_selector_mutation_supported": False,
            "host_mode_selector_status": (
                "HOST_MODE_SELECTOR_UNAVAILABLE" if codex_host else "NOT_APPLICABLE"
            ),
            "existing_plan_authority_behavior": (
                "VERIFY_AND_PROJECT_WITHOUT_REWRITE_OR_DUPLICATION"
            ),
            "task_list_sha256": task_list_sha256,
            "row_start": task_list[0]["number"] if task_list else None,
            "row_end": task_list[-1]["number"] if task_list else None,
            "sole_active_row": active_rows[0]["number"] if active_rows else None,
            "sole_active_task_id": (active_rows[0]["task_id"] if active_rows else None),
            "physically_final_hil_row": (
                physically_final_hil["number"] if physically_final_hil else None
            ),
            "physically_final_hil_task_id": (
                physically_final_hil["task_id"] if physically_final_hil else None
            ),
            "plan_projection_native_reads": [
                "pv_status",
                "pv_task_backlog",
                "pv_query",
            ],
            "bounded_query_required": True,
            "host_plan_tool": "update_plan",
            "host_plan_projection_count": 2,
            "host_plan_acceptance_required": True,
            "host_plan_acceptance_control_must_be_visible": True,
            "host_plan_automatic_acceptance_allowed": False,
            "host_plan_acceptance_is_evidence_lane_hil": False,
            "host_plan_acceptance_pointer_effect": "NONE",
            "phase_4_or_5_before_plan_acceptance_allowed": False,
            "goal_action": "CREATE_OR_RESUME_TRANSFERRED_PLUGIN_GOAL",
            "goal_start_prompt": str(goal_projection["goal_start_prompt"]),
            "pre_goal_source_work_allowed": False,
            "phase_receipts_required_in_order": [
                "DESTINATION_CREATED_AND_BOUND",
                "BOOT_FLASH_AND_RESUME_VERIFIED",
                "HOST_PLAN_PROJECTED_AND_EXPLICITLY_ACCEPTED",
                "EVIDENCE_PLAN_VERIFIED",
                "GOAL_STARTED_OR_RESUMED",
            ],
            "ordered_phases": [
                {
                    "number": 1,
                    "phase": "CREATE_AND_BIND_FRESH_DESTINATION_TASK",
                    "owner": "CODEX_HOST_ORCHESTRATOR",
                    "action": "CONTINUE_IN_NEW_CHAT",
                    "programmatic_when_supported": True,
                    "user_click_required": False,
                    "exactly_once": True,
                    "bind": source_task_binding,
                    "fail_closed_without_host_capability": True,
                    "app_restart_allowed": False,
                    "renderer_reload_allowed": False,
                    "full_thread_history_hydration_allowed": False,
                    "collaboration_overlay_hydration_allowed": False,
                    "thread_hydration_mode": ("BOUNDED_HANDOFF_ENVELOPE_ONLY"),
                    "host_continuity_proof_required_before_phase_2": True,
                },
                {
                    "number": 2,
                    "phase": "ATOMIC_BOOT_FLASH_AND_RESUME_EXACTLY_ONCE",
                    "owner": "NATIVE_EVIDENCE_LANE",
                    "action": "pv_state_travel_resume",
                    "first_state_travel_lifecycle_action": True,
                    "retry_allowed": False,
                    "requires": [
                        "DESTINATION_CREATION_AND_BINDING_RECEIPT",
                        "HANDOFF_RECEIPT",
                        "ACCEPTED_POINTER_AND_PACKAGE",
                        "RUNTIME_DOCTOR_AND_LOCKED_FLASH",
                        "LIVE_SOURCE_IDENTITY",
                        "EXECUTION_PROFILE",
                        "CANONICAL_PLAN_AUTHORITY",
                        "SOLE_ACTIVE_ROW",
                        "PHYSICALLY_FINAL_HIL_ROW_WHEN_DECLARED",
                        "HOST_PROCESS_AND_INITIAL_SHELL_CONTINUITY",
                    ],
                },
                {
                    "number": 3,
                    "phase": "RESTORE_HOST_PLAN_AND_WAIT_FOR_EXPLICIT_ACCEPTANCE",
                    "owner": "ACTIVE_STATE_TRAVEL_SKILL",
                    "action": "update_plan",
                    "projection": "COMPLETE_UNABRIDGED_TASK_LIST",
                    "surface_acceptance_control": True,
                    "automatic_acceptance_allowed": False,
                    "wait_state": "WAITING_FOR_EXPLICIT_HOST_PLAN_ACCEPTANCE",
                    "evidence_lane_hil": False,
                    "pointer_effect": "NONE",
                    "blocks_phases": [4, 5],
                },
                {
                    "number": 4,
                    "phase": "VERIFY_EVIDENCE_PLAN_AFTER_HOST_ACCEPTANCE",
                    "owner": "ACTIVE_STATE_TRAVEL_SKILL",
                    "skill": "evidence-lane-plugin:source-command-evi-plan",
                    "requires_host_plan_acceptance": True,
                    "native_reads": [
                        "pv_status",
                        "pv_task_backlog",
                        "pv_query",
                    ],
                    "bounded_query_required": True,
                    "action": "update_plan",
                    "plan_lane_write_allowed_when_authority_exists": False,
                    "duplicate_rows_allowed": False,
                    "manual_command_required": False,
                },
                {
                    "number": 5,
                    "phase": "START_OR_RESUME_TRANSFERRED_PLUGIN_GOAL",
                    "owner": "CODEX_HOST",
                    "action": "create_or_resume_goal",
                    "requires_completed_phases": [1, 2, 3, 4],
                    "manual_prompt_required": False,
                },
            ],
        }
        body = {
            "schema": "evidence-lane.state-travel-resume-contract.v1",
            "project_id": project_id,
            "travel_mode": travel_mode,
            "plan_authority": "PLAN_LANE",
            "task_list_source": task_list_source,
            "task_list": task_list,
            "task_list_sha256": task_list_sha256,
            "resume_step": resume_step,
            "additive_deltas": additive_deltas,
            "additive_deltas_sha256": sha256_bytes(
                canonical_json_bytes(additive_deltas)
            ),
            "steer_default_boundary": "BEFORE_NEXT_HIL",
            "linked_steer_policy": "APPEND_TO_EXISTING_STEP_WITHOUT_REPLACEMENT",
            "unlinked_steer_policy": (
                "INSERT_NEW_STEP_BEFORE_NEXT_HIL_AND_INCREASE_COUNT"
            ),
            "panel_reactivation": panel_reactivation,
            "task_panel_persistent_until": (
                "HUMAN_MARKS_GOAL_COMPLETE_OR_EXPLICIT_TASK_STATE_TRAVEL_HANDOFF_PASSES"
            ),
            "execution_profile": execution_profile,
            "execution_profile_match_required": bool(execution_profile),
            "host_settings_mutation_supported": False,
            "host_profile_application": "HOST_MEDIATED_EXACT_MATCH_REQUIRED",
            "execution_writer_boundary": execution_writer_boundary,
            "goal_continuity": goal_continuity,
            "source_task_binding": source_task_binding,
            "destination_orchestration": destination_orchestration,
            "collaboration_law": {
                "writer_policy": "SOLE_WRITER",
                "entry_recovery_subagents": (
                    "READ_ONLY_ONLY_AT_GENUINE_STATE_TRAVEL_ENTRY"
                ),
                "later_subagents": "EXPLICIT_USER_COMMAND_ONLY",
                "alternate_checkout_writer": ("FORBIDDEN_UNLESS_EXPLICIT_USER_CHANGE"),
                "background_mutation": ("FORBIDDEN_UNLESS_EXPLICIT_USER_CHANGE"),
                "browser_profile": (
                    "ONE_USER_SELECTED_PROFILE_ONLY_UNLESS_EXPLICIT_USER_CHANGE"
                ),
            },
            "host_universe": (
                {
                    "kind": "CODEX",
                    "plan_mode_shortcut": "/pl",
                    "plugin_plan_command": "/evi-plan",
                    "native_goal_projection": True,
                    "native_task_panel_projection": True,
                    "goal_or_model_selector_mutation_supported_by_mcp": False,
                    "host_mode_selector_status": "HOST_MODE_SELECTOR_UNAVAILABLE",
                    "state_travel_destination_plan_projection": (
                        "AUTOMATIC_HOST_EQUIVALENT"
                    ),
                    "state_travel_manual_plan_command_required": False,
                }
                if session.host.value.startswith("CODEX")
                else {
                    "kind": "UNSUPPORTED_NON_CODEX_HOST",
                    "codex_plan_mode_controls_applicable": False,
                    "codex_goal_or_task_panel_applicable": False,
                    "append_only_lane_and_env_laws_preserved": True,
                }
            ),
            "suggested_next_prompt": suggested_next_prompt,
            "private_reasoning_stored": False,
        }
        return {
            **body,
            "resume_contract_sha256": sha256_bytes(canonical_json_bytes(body)),
        }

    def validate_state_travel_destination(
        self,
        project_id: str,
        session_id: str,
        *,
        handoff_id: str,
        host: HostKind | str,
        host_session_id: str,
        runtime_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Fail before host rebinding when the prepared handoff cannot match."""

        session = self.load(project_id, session_id)
        travel = session.metadata.get("state_travel")
        require(
            isinstance(travel, dict) and travel.get("status") == "PREPARED",
            "STATE_TRAVEL_HANDOFF_NOT_PREPARED",
            "No prepared State Travel handoff exists for this governed session.",
            status="BLOCKED",
        )
        travel = cast(dict[str, Any], travel)
        require(
            handoff_id == travel.get("handoff_id"),
            "STATE_TRAVEL_HANDOFF_ID_MISMATCH",
            "The State Travel handoff ID does not match the sealed receipt.",
            status="MISMATCH",
            provided=handoff_id,
        )
        exact_host_session_id = host_session_id.strip()
        require(
            bool(exact_host_session_id)
            and exact_host_session_id
            != str(travel.get("origin_host_session_id") or ""),
            "STATE_TRAVEL_NEW_HOST_WINDOW_REQUIRED",
            "State Travel must resume in a fresh host task.",
            status="BLOCKED",
            target_surface=travel.get("target_surface"),
        )
        host_kind = normalize_host_kind(host)
        if travel.get("target_surface") == "NEW_CODEX_TASK":
            require(
                host_kind
                in {HostKind.CODEX_DESKTOP, HostKind.CODEX_CLI, HostKind.CODEX_VM},
                "STATE_TRAVEL_HOST_KIND_MISMATCH",
                "This handoff requires a fresh Codex task.",
                status="MISMATCH",
                host=host_kind.value,
            )
        resume_contract = cast(
            dict[str, Any],
            travel.get("resume_contract") or {},
        )
        expected_profile = cast(
            dict[str, str], resume_contract.get("execution_profile", {})
        )
        actual_profile = execution_profile_from_context(runtime_context)
        mismatches = execution_profile_mismatches(expected_profile, actual_profile)
        require(
            not mismatches,
            "STATE_TRAVEL_EXECUTION_PROFILE_MISMATCH",
            "The destination task does not use the exact prepared model, submodel, "
            "reasoning effort, and speed profile. Change the host-owned selectors "
            "and retry State Travel.",
            status="MISMATCH",
            mismatches=mismatches,
            host_settings_mutation_supported=False,
        )
        destination_orchestration = cast(
            dict[str, Any],
            resume_contract.get("destination_orchestration") or {},
        )
        destination_task_binding: dict[str, Any] | None = None
        if destination_orchestration.get("enabled") is True:
            runtime = runtime_context or {}
            creation = runtime.get("state_travel_destination_creation")
            require(
                isinstance(creation, dict)
                and creation.get("capability_status") == "SUPPORTED",
                "STATE_TRAVEL_HOST_CONTINUE_IN_NEW_CHAT_UNAVAILABLE",
                "The host did not prove supported programmatic Continue in new chat; "
                "the destination must fail closed before resume.",
                status="BLOCKED",
                capability_status=(
                    creation.get("capability_status")
                    if isinstance(creation, dict)
                    else "UNAVAILABLE"
                ),
            )
            creation = cast(dict[str, Any], creation)
            source_binding = cast(
                dict[str, Any],
                resume_contract.get("source_task_binding") or {},
            )
            source_binding_body = {
                key: value
                for key, value in source_binding.items()
                if key != "binding_sha256"
            }
            source_task_id = str(source_binding.get("source_task_id") or "")
            source_task_deep_link = self._state_travel_task_deep_link(
                str(travel.get("origin_host") or ""),
                source_task_id,
            )
            destination_task_deep_link = self._state_travel_task_deep_link(
                host_kind.value,
                exact_host_session_id,
            )
            destination_resolution = self._state_travel_destination_resolution(
                creation,
                destination_task_id=exact_host_session_id,
                destination_task_deep_link=destination_task_deep_link,
            )
            host_continuity = normalize_destination_host_continuity(
                creation.get("host_continuity"),
                source_task_id=source_task_id,
                source_task_deep_link=source_task_deep_link,
                destination_task_id=exact_host_session_id,
                destination_task_deep_link=destination_task_deep_link,
            )
            require(
                source_binding.get("binding_sha256")
                == sha256_bytes(canonical_json_bytes(source_binding_body))
                and source_task_id == str(travel.get("origin_host_session_id") or "")
                and source_binding.get("source_task_deep_link") == source_task_deep_link
                and creation.get("schema")
                == "evidence-lane.host-destination-creation.v1"
                and creation.get("host_action") == "CONTINUE_IN_NEW_CHAT"
                and creation.get("programmatic") is True
                and creation.get("creation_count") == 1
                and creation.get("source_task_id") == source_task_id
                and creation.get("source_task_deep_link") == source_task_deep_link
                and creation.get("canonical_title_increment_verified") is True,
                "STATE_TRAVEL_DESTINATION_CREATION_BINDING_MISMATCH",
                "The host destination-creation receipt does not bind exactly one "
                "fresh source/destination task pair and canonical title increment.",
                status="MISMATCH",
            )
            current_plugin_build = self._state_travel_plugin_build_identity()
            require(
                current_plugin_build == source_binding.get("plugin_build"),
                "STATE_TRAVEL_DESTINATION_PLUGIN_BUILD_MISMATCH",
                "The destination task is not running the exact prepared plugin build.",
                status="MISMATCH",
                expected=source_binding.get("plugin_build"),
                actual=current_plugin_build,
            )
            binding_body = {
                "schema": "evidence-lane.state-travel-destination-task-binding.v1",
                "project_id": project_id,
                "evidence_session_id": session_id,
                "source_task_id": source_task_id,
                "source_task_deep_link": source_task_deep_link,
                "destination_task_id": exact_host_session_id,
                "destination_task_deep_link": destination_task_deep_link,
                "destination_resolution": destination_resolution,
                "host_continuity": host_continuity,
                "host_action": creation.get("host_action"),
                "creation_count": creation.get("creation_count"),
                "canonical_title_increment_verified": True,
                "accepted_pointer": source_binding.get("accepted_pointer"),
                "active_row": source_binding.get("active_row"),
                "active_task_id": source_binding.get("active_task_id"),
                "source_identity_sha256": source_binding.get("source_identity_sha256"),
                "source_worktree_sha256": source_binding.get("source_worktree_sha256"),
                "plugin_build": current_plugin_build,
                "execution_profile": actual_profile,
                "identity_basis": source_binding.get("identity_basis"),
                "task_title_used_as_identity": False,
                "cwd_used_as_identity": False,
            }
            destination_task_binding = {
                **binding_body,
                "binding_sha256": sha256_bytes(canonical_json_bytes(binding_body)),
            }
        pointer = self.store.pointer(project_id)
        require(
            pointer.accepted_pv == travel.get("accepted_pv")
            and pointer.generation == travel.get("pointer_generation")
            and pointer.accepted_manifest_sha256 == travel.get("manifest_sha256"),
            "STATE_TRAVEL_POINTER_VERIFICATION_FAILED",
            "The verified pointer base changed after the handoff was prepared.",
            status="STALE",
            actual=pointer.as_dict(),
        )
        prepared_task = cast(dict[str, Any], travel.get("task_snapshot") or {})
        current_task = {
            "state": session.state.value,
            "task": session.task,
            "task_sha256": sha256_bytes(canonical_json_bytes(session.task)),
            "pending_task": session.metadata.get("pending_task"),
            "pending_task_sha256": sha256_bytes(
                canonical_json_bytes(session.metadata.get("pending_task"))
            ),
            "candidate_id": session.candidate_id,
            "active_backlog_task_id": session.metadata.get("active_backlog_task_id"),
            "run_id": session.metadata.get("run_id"),
        }
        current_task["snapshot_sha256"] = sha256_bytes(
            canonical_json_bytes(current_task)
        )
        require(
            current_task["snapshot_sha256"] == prepared_task.get("snapshot_sha256"),
            "STATE_TRAVEL_UNFINISHED_TASK_MISMATCH",
            "The governed task changed after the handoff was prepared.",
            status="MISMATCH",
        )
        prepared_candidate = travel.get("candidate_snapshot")
        if isinstance(prepared_candidate, dict):
            require(
                self._state_travel_candidate_snapshot(project_id, session.candidate_id)
                == prepared_candidate,
                "STATE_TRAVEL_CANDIDATE_MISMATCH",
                "The pending candidate bytes changed after the handoff was prepared.",
                status="MISMATCH",
            )
        prepared_plan = cast(dict[str, Any], travel.get("plan_snapshot") or {})
        require(
            self._state_travel_plan_snapshot(project_id)["snapshot_sha256"]
            == prepared_plan.get("snapshot_sha256"),
            "STATE_TRAVEL_PLAN_LANE_MISMATCH",
            "The Plan Lane changed after the handoff was prepared.",
            status="MISMATCH",
        )
        prepared_source = travel.get("source_snapshot")
        if isinstance(prepared_source, dict):
            require(
                self._state_travel_source_snapshot(project_id)["identity_sha256"]
                == prepared_source.get("identity_sha256"),
                "STATE_TRAVEL_LIVE_SOURCE_MISMATCH",
                "The live source bytes changed after the handoff was prepared.",
                status="MISMATCH",
            )
        return {
            "status": "PASS",
            "destination_host": host_kind.value,
            "destination_host_session_id": exact_host_session_id,
            "execution_profile": actual_profile,
            "execution_profile_verified": bool(expected_profile),
            "host_settings_mutated": False,
            "destination_task_binding": destination_task_binding,
            "destination_task_binding_verified": bool(destination_task_binding),
        }

    def state_travel_resume_replay(
        self,
        project_id: str,
        session_id: str,
        *,
        handoff_id: str,
        host: HostKind | str,
        host_session_id: str,
        runtime_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Return a no-rebind replay receipt for one already-consumed handoff."""

        session = self.load(project_id, session_id)
        current = session.metadata.get("state_travel")
        history = session.metadata.get("state_travel_history", [])
        receipts = [current] if isinstance(current, dict) else []
        if isinstance(history, list):
            receipts.extend(row for row in history if isinstance(row, dict))
        matches = [
            cast(dict[str, Any], row)
            for row in receipts
            if row.get("handoff_id") == handoff_id
        ]
        if not matches:
            return None
        if (
            isinstance(current, dict)
            and current.get("handoff_id") == handoff_id
            and current.get("status") == "PREPARED"
        ):
            return None
        consumed = next(
            (
                row
                for row in matches
                if row.get("status") in _STATE_TRAVEL_CONSUMED_STATUSES
            ),
            None,
        )
        require(
            consumed is not None,
            "STATE_TRAVEL_HANDOFF_SUPERSEDED",
            "The requested State Travel handoff is historical but was not consumed; "
            "it cannot bind a destination.",
            status="BLOCKED",
            handoff_id=handoff_id,
            historical_statuses=sorted(
                {str(row.get("status") or "UNKNOWN") for row in matches}
            ),
        )
        consumed = cast(dict[str, Any], consumed)
        exact_host_session_id = host_session_id.strip()
        host_kind = normalize_host_kind(host)
        require(
            consumed.get("destination_host") == host_kind.value
            and consumed.get("destination_host_session_id") == exact_host_session_id,
            "STATE_TRAVEL_REPLAY_DESTINATION_MISMATCH",
            "An already-consumed handoff cannot be rebound to another destination "
            "host-session tuple.",
            status="MISMATCH",
            handoff_id=handoff_id,
            expected_destination_host=consumed.get("destination_host"),
            expected_destination_host_session_id=consumed.get(
                "destination_host_session_id"
            ),
            supplied_destination_host=host_kind.value,
            supplied_destination_host_session_id=exact_host_session_id,
        )
        resume_contract = cast(
            dict[str, Any],
            consumed.get("resume_contract") or {},
        )
        expected_profile = cast(
            dict[str, str],
            resume_contract.get("execution_profile") or {},
        )
        actual_profile = execution_profile_from_context(runtime_context)
        mismatches = execution_profile_mismatches(expected_profile, actual_profile)
        require(
            not mismatches,
            "STATE_TRAVEL_REPLAY_EXECUTION_PROFILE_MISMATCH",
            "An already-consumed handoff cannot be replayed under a different "
            "execution profile.",
            status="MISMATCH",
            mismatches=mismatches,
        )
        destination_binding = cast(
            dict[str, Any],
            consumed.get("destination_task_binding") or {},
        )
        if destination_binding:
            require(
                destination_binding.get("destination_task_id") == exact_host_session_id,
                "STATE_TRAVEL_REPLAY_DESTINATION_BINDING_MISMATCH",
                "The consumed destination-task binding does not match the replay "
                "host-session tuple.",
                status="MISMATCH",
            )
            creation = (runtime_context or {}).get("state_travel_destination_creation")
            require(
                isinstance(creation, dict),
                "STATE_TRAVEL_REPLAY_DESTINATION_CREATION_REQUIRED",
                "Replay classification requires the original destination-creation "
                "identity without invoking it again.",
                status="BLOCKED",
            )
            supplied_resolution = self._state_travel_destination_resolution(
                cast(dict[str, Any], creation),
                destination_task_id=exact_host_session_id,
                destination_task_deep_link=cast(
                    str | None,
                    destination_binding.get("destination_task_deep_link"),
                ),
            )
            stored_resolution = destination_binding.get("destination_resolution")
            require(
                not isinstance(stored_resolution, dict)
                or (
                    stored_resolution.get("client_thread_id")
                    == supplied_resolution.get("client_thread_id")
                    and stored_resolution.get("destination_task_id")
                    == supplied_resolution.get("destination_task_id")
                ),
                "STATE_TRAVEL_REPLAY_CLIENT_THREAD_BINDING_MISMATCH",
                "The replay supplied a different queued clientThreadId resolution.",
                status="MISMATCH",
            )

        pointer_before = self.store.pointer(project_id).as_dict()
        candidate_before = session.candidate_id
        pending_hil_before = bool(session.metadata.get("pending_hil"))
        incident_body = {
            "schema": "evidence-lane.state-travel-replay-incident.v1",
            "incident_id": prefixed_id("state_travel_replay"),
            "status": "ALREADY_CONSUMED_NO_REBIND",
            "project_id": project_id,
            "session_id": session_id,
            "handoff_id": handoff_id,
            "handoff_sha256": consumed.get("handoff_sha256"),
            "original_consumption_receipt_sha256": cast(
                dict[str, Any],
                consumed.get("resume_consumption_receipt") or {},
            ).get("receipt_sha256"),
            "destination_host": host_kind.value,
            "destination_host_session_id": exact_host_session_id,
            "resume_invoked": False,
            "host_rebound": False,
            "source_mutated": False,
            "pointer_moved": False,
            "candidate_created": False,
            "candidate_mutated": False,
            "pending_hil_mutated": False,
            "hil_inferred": False,
            "pointer_before": pointer_before,
            "candidate_id_before": candidate_before,
            "pending_hil_before": pending_hil_before,
            "recorded_at": utc_now(),
        }
        incident = {
            **incident_body,
            "incident_sha256": sha256_bytes(canonical_json_bytes(incident_body)),
        }
        session.metadata.setdefault("state_travel_replay_history", []).append(incident)
        session.metadata["last_state_travel_replay_incident"] = incident
        self._save(session)
        pointer_after = self.store.pointer(project_id).as_dict()
        require(
            pointer_after == pointer_before
            and session.candidate_id == candidate_before
            and bool(session.metadata.get("pending_hil")) == pending_hil_before,
            "STATE_TRAVEL_REPLAY_INCIDENT_INVARIANT_FAILED",
            "Replay incident recording changed governed lifecycle authority.",
            status="FAIL",
        )
        return {
            "status": "ALREADY_CONSUMED_NO_REBIND",
            "state_travel": consumed,
            "idempotent_reuse": True,
            "replay_incident": incident,
            "session": session.as_dict(),
            "pointer": pointer_after,
            "wait_state": consumed.get("wait_state"),
            "next_action": "RETURN_ORIGINAL_CONSUMPTION_RECEIPT_NO_REBIND",
            "suggested_next_prompt": cast(
                dict[str, Any],
                consumed.get("next_action_contract") or {},
            ).get("suggested_next_prompt"),
            "next_action_contract": consumed.get("next_action_contract"),
            "task_started": False,
            "continuation_ready": consumed.get("continuation_ready"),
            "boot_repeated": False,
            "flash_repeated": False,
        }

    def prepare_state_travel(
        self,
        project_id: str,
        session_id: str,
        *,
        resume_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Seal either accepted entry or exact verified unfinished work."""

        session = self.load(project_id, session_id)
        require(
            not session.metadata.get("closed_at")
            and session.state not in _TERMINAL_STATES,
            "STATE_TRAVEL_PREPARE_STATE_INVALID",
            "State Travel requires one active non-terminal governed session.",
            status="BLOCKED",
            state=session.state.value,
        )
        pointer = self.store.pointer(project_id)
        require(
            pointer.accepted_pv == session.accepted_pv
            and pointer.generation == session.accepted_pointer_generation,
            "STATE_TRAVEL_POINTER_STALE",
            "State Travel requires the session and verified pointer base to match.",
            status="STALE",
            pointer=pointer.as_dict(),
            session_accepted_pv=session.accepted_pv,
            session_pointer_generation=session.accepted_pointer_generation,
        )
        requested_mode = str((resume_contract or {}).get("entry_mode") or "").upper()
        has_unfinished_work = session.state not in {
            SessionState.PVN_ACCEPTED,
            SessionState.PVN1_ACCEPTED,
        }
        travel_mode = requested_mode or (
            "UNFINISHED_VERIFIED_WORK" if has_unfinished_work else "ACCEPTED_ENTRY"
        )
        require(
            travel_mode in {"ACCEPTED_ENTRY", "UNFINISHED_VERIFIED_WORK"},
            "STATE_TRAVEL_MODE_INVALID",
            "State Travel supports exact unfinished work or an explicit accepted entry.",
            status="BLOCKED",
            travel_mode=travel_mode,
        )
        require(
            travel_mode != "ACCEPTED_ENTRY" or pointer.accepted_pv is not None,
            "STATE_TRAVEL_ACCEPTED_ENTRY_MISSING",
            "Accepted-entry State Travel requires an accepted pointer.",
            status="BLOCKED",
        )
        accepted_validation: dict[str, Any] | None = None
        if pointer.accepted_pv:
            accepted_validation = self.store.validate_accepted(
                project_id,
                pointer.accepted_pv,
                require_promotable=False,
            )
            require(
                accepted_validation["manifest_sha256"]
                == pointer.accepted_manifest_sha256,
                "STATE_TRAVEL_ACCEPTED_POINTER_HASH_MISMATCH",
                "The verified pointer base does not match the accepted package.",
                status="MISMATCH",
            )
        source_snapshot = (
            self._state_travel_source_snapshot(project_id)
            if travel_mode == "UNFINISHED_VERIFIED_WORK"
            else None
        )
        exact_resume_contract = self._state_travel_resume_contract(
            project_id,
            session,
            resume_contract,
            travel_mode=travel_mode,
            source_snapshot=source_snapshot,
        )
        candidate_snapshot = self._state_travel_candidate_snapshot(
            project_id,
            session.candidate_id,
        )
        task_snapshot = {
            "state": session.state.value,
            "task": session.task,
            "task_sha256": sha256_bytes(canonical_json_bytes(session.task)),
            "pending_task": session.metadata.get("pending_task"),
            "pending_task_sha256": sha256_bytes(
                canonical_json_bytes(session.metadata.get("pending_task"))
            ),
            "candidate_id": session.candidate_id,
            "active_backlog_task_id": session.metadata.get("active_backlog_task_id"),
            "run_id": session.metadata.get("run_id"),
        }
        task_snapshot["snapshot_sha256"] = sha256_bytes(
            canonical_json_bytes(task_snapshot)
        )
        plan_snapshot = self._state_travel_plan_snapshot(project_id)
        pointer_body = pointer.as_dict()
        pointer_snapshot = {
            **pointer_body,
            "pointer_sha256": sha256_bytes(canonical_json_bytes(pointer_body)),
        }
        snapshot_body = {
            "travel_mode": travel_mode,
            "task_snapshot_sha256": task_snapshot["snapshot_sha256"],
            "candidate_snapshot": candidate_snapshot,
            "plan_snapshot_sha256": plan_snapshot["snapshot_sha256"],
            "source_identity_sha256": (
                source_snapshot["identity_sha256"] if source_snapshot else None
            ),
            "pointer_sha256": pointer_snapshot["pointer_sha256"],
            "resume_contract_sha256": exact_resume_contract["resume_contract_sha256"],
        }
        verified_snapshot_sha256 = sha256_bytes(canonical_json_bytes(snapshot_body))
        existing = session.metadata.get("state_travel")
        superseded_prepared: dict[str, Any] | None = None
        supersession_disposition: dict[str, Any] | None = None
        supersession_event_type: str | None = None
        if isinstance(existing, dict) and existing.get("status") == "PREPARED":
            if existing.get("verified_snapshot_sha256") != verified_snapshot_sha256:
                supplied = resume_contract or {}
                supersede_handoff_id = str(
                    supplied.get("supersede_prepared_handoff_id") or ""
                )
                supersede_reason = str(supplied.get("supersede_prepared_reason") or "")
                require(
                    supersede_handoff_id == str(existing.get("handoff_id") or "")
                    and supersede_reason == "EXPLICIT_USER_CORRECTION",
                    "STATE_TRAVEL_PREPARED_CONTRACT_MISMATCH",
                    "A different State Travel handoff is already prepared. Consume the "
                    "exact receipt or explicitly resolve it before preparing another.",
                    status="MISMATCH",
                )
                current_host_session_id = str(
                    session.metadata.get("current_host_session_id") or ""
                )
                origin_host_session_id = str(
                    existing.get("origin_host_session_id") or ""
                )
                same_host_correction = bool(current_host_session_id) and (
                    current_host_session_id == origin_host_session_id
                )
                if same_host_correction:
                    disposition_status = "SUPERSEDED_BY_SAME_HOST_USER_CORRECTION"
                    supersession_event_type = (
                        "pv.state_travel.superseded_same_host_user_correction"
                    )
                else:
                    orphan_handoff_sha256 = str(
                        supplied.get("supersede_prepared_handoff_sha256") or ""
                    )
                    orphan_origin_host_session_id = str(
                        supplied.get("supersede_prepared_origin_host_session_id") or ""
                    )
                    orphan_scope = str(supplied.get("supersede_prepared_scope") or "")
                    orphan_confirmation = str(
                        supplied.get("supersede_prepared_confirmation") or ""
                    )
                    require(
                        bool(current_host_session_id)
                        and bool(origin_host_session_id)
                        and current_host_session_id != origin_host_session_id
                        and orphan_handoff_sha256
                        == str(existing.get("handoff_sha256") or "")
                        and orphan_origin_host_session_id == origin_host_session_id
                        and orphan_scope == "ORPHANED_STALE_HOST_TASK"
                        and orphan_confirmation
                        == "SUPERSEDE_ORPHANED_PREPARED_HANDOFF",
                        "STATE_TRAVEL_PREPARED_ORPHAN_CORRECTION_INVALID",
                        "Cross-host replacement requires the exact stale prepared "
                        "handoff identity, its origin host identity, and an explicit "
                        "user orphan-disposition confirmation.",
                        status="BLOCKED",
                        current_host_session_id=current_host_session_id or None,
                        origin_host_session_id=origin_host_session_id or None,
                        supplied_handoff_sha256=(orphan_handoff_sha256 or None),
                        expected_handoff_sha256=(
                            existing.get("handoff_sha256") or None
                        ),
                        supplied_origin_host_session_id=(
                            orphan_origin_host_session_id or None
                        ),
                        supplied_scope=orphan_scope or None,
                    )
                    disposition_status = "SUPERSEDED_BY_EXPLICIT_ORPHAN_USER_CORRECTION"
                    supersession_event_type = (
                        "pv.state_travel.superseded_orphan_user_correction"
                    )
                superseded_prepared = dict(existing)
                supersession_disposition = {
                    "schema": "evidence-lane.state-travel-disposition.v1",
                    "status": disposition_status,
                    "handoff_id": existing.get("handoff_id"),
                    "handoff_sha256": existing.get("handoff_sha256"),
                    "supersede_reason": supersede_reason,
                    "host_session_id": current_host_session_id,
                    "origin_host_session_id": origin_host_session_id,
                    "replacement_host_session_id": current_host_session_id,
                    "orphaned_stale_host_task": not same_host_correction,
                    "pointer_moved": False,
                    "state_travel_consumed": False,
                    "superseded_at": utc_now(),
                }
            else:
                existing_contract = cast(
                    dict[str, Any], existing["next_action_contract"]
                )
                return {
                    "status": "PASS",
                    "state_travel": existing,
                    "idempotent_reuse": True,
                    "host_window_opened": False,
                    "next_action": existing["next_action"],
                    "suggested_next_prompt": existing_contract["suggested_next_prompt"],
                    "next_action_contract": existing_contract,
                }
        target_surface, next_action = self._state_travel_target(session)
        receipt: dict[str, Any] = {
            "schema": "evidence-lane.state-travel.v2",
            "handoff_id": prefixed_id("travel"),
            "project_id": project_id,
            "session_id": session_id,
            "status": "PREPARED",
            "origin_host": session.host.value,
            "origin_host_session_id": session.metadata.get("current_host_session_id"),
            "target_surface": target_surface,
            "next_action": next_action,
            "travel_mode": travel_mode,
            "origin_state": session.state.value,
            "accepted_pv": pointer.accepted_pv,
            "pointer_generation": pointer.generation,
            "manifest_sha256": (
                accepted_validation["manifest_sha256"] if accepted_validation else None
            ),
            "package_sha256": (
                accepted_validation["package_sha256"] if accepted_validation else None
            ),
            "pointer_snapshot": pointer_snapshot,
            "task_snapshot": task_snapshot,
            "candidate_snapshot": candidate_snapshot,
            "plan_snapshot": plan_snapshot,
            "source_snapshot": source_snapshot,
            "resume_contract": exact_resume_contract,
            "verified_snapshot_sha256": verified_snapshot_sha256,
            "required_entry_commands": [
                "/evi-state-travel",
                "/evi-boot",
            ],
            "next_action_contract": state_travel_next_action(
                state=next_action,
                command="/evi-state-travel",
                suggested_next_prompt="/evi-state-travel",
                target_surface=target_surface,
                task_panel_reactivation=exact_resume_contract.get("panel_reactivation"),
                execution_writer_boundary=exact_resume_contract.get(
                    "execution_writer_boundary"
                ),
                goal_continuity=exact_resume_contract.get("goal_continuity"),
                destination_orchestration=exact_resume_contract.get(
                    "destination_orchestration"
                ),
            ),
            "host_window_opened": False,
            "host_window_opening_is_host_mediated": True,
            "destination_profile_must_match_before_binding": bool(
                exact_resume_contract["execution_profile"]
            ),
            "host_settings_mutation_supported": False,
            "accepted_entry_from_unfinished_state": (
                travel_mode == "ACCEPTED_ENTRY" and has_unfinished_work
            ),
            "prepared_at": utc_now(),
        }
        if supersession_disposition is not None:
            receipt["supersedes_prepared_handoff"] = supersession_disposition
        receipt["handoff_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
        if superseded_prepared is not None:
            session.metadata.setdefault("state_travel_history", []).append(
                superseded_prepared
            )
        session.metadata["state_travel"] = receipt
        self._save(session)
        lineage = ChatLineage(self._lineage_path(project_id, session_id))
        supersession_event = None
        if supersession_disposition is not None:
            require(
                supersession_event_type is not None,
                "STATE_TRAVEL_SUPERSESSION_EVENT_TYPE_REQUIRED",
                "State Travel supersession requires one exact visible event type.",
                status="MISMATCH",
            )
            assert supersession_event_type is not None
            supersession_event = lineage.append(
                event_type=supersession_event_type,
                visible_payload=supersession_disposition,
                occurred_at=supersession_disposition["superseded_at"],
                session_id=session_id,
            )
        event = lineage.append(
            event_type="pv.state_travel.prepared",
            visible_payload=receipt,
            occurred_at=receipt["prepared_at"],
            session_id=session_id,
        )
        return {
            "status": "PASS",
            "state_travel": receipt,
            "idempotent_reuse": False,
            "host_window_opened": False,
            "next_action": next_action,
            "suggested_next_prompt": receipt["next_action_contract"][
                "suggested_next_prompt"
            ],
            "next_action_contract": receipt["next_action_contract"],
            "event": event,
            "supersession_event": supersession_event,
        }

    def complete_state_travel(
        self,
        project_id: str,
        session_id: str,
        *,
        handoff_id: str,
        flash: dict[str, Any],
        destination_runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Verify and enter accepted context or resume the exact unfinished step."""

        session = self.load(project_id, session_id)
        travel = session.metadata.get("state_travel")
        require(
            isinstance(travel, dict) and travel.get("status") == "PREPARED",
            "STATE_TRAVEL_HANDOFF_NOT_PREPARED",
            "No prepared State Travel handoff exists for this governed session.",
            status="BLOCKED",
        )
        travel = cast(dict[str, Any], travel)
        current_host_session_id = str(
            session.metadata.get("current_host_session_id") or ""
        )
        destination = self.validate_state_travel_destination(
            project_id,
            session_id,
            handoff_id=handoff_id,
            host=session.host,
            host_session_id=current_host_session_id,
            runtime_context=destination_runtime_context,
        )
        pointer = self.store.pointer(project_id)
        require(
            pointer.accepted_pv == travel.get("accepted_pv")
            and pointer.generation == travel.get("pointer_generation")
            and pointer.accepted_manifest_sha256 == travel.get("manifest_sha256"),
            "STATE_TRAVEL_POINTER_VERIFICATION_FAILED",
            "The verified pointer base changed after the handoff was sealed.",
            status="STALE",
            expected={
                "accepted_pv": travel.get("accepted_pv"),
                "generation": travel.get("pointer_generation"),
                "manifest_sha256": travel.get("manifest_sha256"),
            },
            actual=pointer.as_dict(),
        )
        validation: dict[str, Any] | None = None
        if pointer.accepted_pv:
            validation = self.store.validate_accepted(
                project_id,
                pointer.accepted_pv,
                require_promotable=False,
            )
            require(
                validation["manifest_sha256"] == travel.get("manifest_sha256")
                and validation["package_sha256"] == travel.get("package_sha256"),
                "STATE_TRAVEL_ACCEPTED_PACKAGE_MISMATCH",
                "The accepted pointer-base bytes do not match the handoff.",
                status="MISMATCH",
            )
        require(
            flash.get("status") == "PASS",
            "STATE_TRAVEL_FLASH_VERIFICATION_FAILED",
            "State Travel requires locked ENV/UOP Flash verification.",
            status="BLOCKED",
            flash_status=flash.get("status"),
        )
        travel_mode = str(travel.get("travel_mode") or "ACCEPTED_ENTRY")
        task_snapshot = cast(dict[str, Any], travel.get("task_snapshot") or {})
        current_task_snapshot = {
            "state": session.state.value,
            "task": session.task,
            "task_sha256": sha256_bytes(canonical_json_bytes(session.task)),
            "pending_task": session.metadata.get("pending_task"),
            "pending_task_sha256": sha256_bytes(
                canonical_json_bytes(session.metadata.get("pending_task"))
            ),
            "candidate_id": session.candidate_id,
            "active_backlog_task_id": session.metadata.get("active_backlog_task_id"),
            "run_id": session.metadata.get("run_id"),
        }
        current_task_snapshot["snapshot_sha256"] = sha256_bytes(
            canonical_json_bytes(current_task_snapshot)
        )
        require(
            current_task_snapshot["snapshot_sha256"]
            == task_snapshot.get("snapshot_sha256"),
            "STATE_TRAVEL_UNFINISHED_TASK_MISMATCH",
            "The governed lifecycle task changed after State Travel was prepared.",
            status="MISMATCH",
            expected_state=task_snapshot.get("state"),
            actual_state=session.state.value,
        )
        candidate_snapshot = travel.get("candidate_snapshot")
        if isinstance(candidate_snapshot, dict):
            current_candidate = self._state_travel_candidate_snapshot(
                project_id,
                session.candidate_id,
            )
            require(
                current_candidate == candidate_snapshot,
                "STATE_TRAVEL_CANDIDATE_MISMATCH",
                "The pending candidate bytes changed after State Travel was prepared.",
                status="MISMATCH",
            )
        current_plan = self._state_travel_plan_snapshot(project_id)
        prepared_plan = cast(dict[str, Any], travel.get("plan_snapshot") or {})
        require(
            current_plan["snapshot_sha256"] == prepared_plan.get("snapshot_sha256"),
            "STATE_TRAVEL_PLAN_LANE_MISMATCH",
            "The canonical Plan Lane changed after State Travel was prepared.",
            status="MISMATCH",
        )
        source_verified = False
        if travel_mode == "UNFINISHED_VERIFIED_WORK":
            current_source = self._state_travel_source_snapshot(project_id)
            prepared_source = cast(dict[str, Any], travel.get("source_snapshot") or {})
            require(
                current_source["identity_sha256"]
                == prepared_source.get("identity_sha256"),
                "STATE_TRAVEL_LIVE_SOURCE_MISMATCH",
                "The live source bytes changed after State Travel was prepared.",
                status="MISMATCH",
                expected=prepared_source,
                actual=current_source,
            )
            source_verified = True

        resume_contract = cast(dict[str, Any], travel.get("resume_contract") or {})
        accepted_state_origin = travel.get("origin_state") in {
            SessionState.PVN_ACCEPTED.value,
            SessionState.PVN1_ACCEPTED.value,
        }
        if travel_mode == "ACCEPTED_ENTRY" and accepted_state_origin:
            entry = self.begin_next_turn(
                project_id,
                session_id,
                _state_travel_handoff_id=handoff_id,
            )
            session = self.load(project_id, session_id)
            state_travel_status = "VERIFIED_WAITING"
            wait_state: str | None = "WAITING_FOR_NEXT_USER_COMMAND"
            next_action = "WAIT_FOR_NEXT_USER_COMMAND"
            next_action_contract = state_travel_next_action(
                state="WAITING_FOR_NEXT_USER_COMMAND",
                command="USER_PROVIDES_NEXT_BOUNDED_TASK",
                suggested_next_prompt=(
                    "Provide the next bounded Evidence Lane task, or run "
                    "/evi-build to inspect governed status."
                ),
                target_surface=str(travel.get("target_surface")),
                task_panel_reactivation=resume_contract.get("panel_reactivation"),
                execution_writer_boundary=resume_contract.get(
                    "execution_writer_boundary"
                ),
                goal_continuity=resume_contract.get("goal_continuity"),
                destination_orchestration=resume_contract.get(
                    "destination_orchestration"
                ),
            )
            continuation_ready = False
        elif travel_mode == "ACCEPTED_ENTRY":
            entry = {
                "accepted_pointer_context_selected": True,
                "unfinished_state_preserved": True,
                "task_cleared": False,
                "candidate_cleared": False,
            }
            state_travel_status = "VERIFIED_WAITING"
            wait_state = "WAITING_FOR_NEXT_USER_COMMAND"
            next_action = "WAIT_FOR_NEXT_USER_COMMAND"
            next_action_contract = state_travel_next_action(
                state="WAITING_FOR_NEXT_USER_COMMAND",
                command="USER_SELECTS_ACCEPTED_CONTEXT_ACTION",
                suggested_next_prompt=str(resume_contract["suggested_next_prompt"]),
                target_surface=str(travel.get("target_surface")),
                task_panel_reactivation=resume_contract.get("panel_reactivation"),
                execution_writer_boundary=resume_contract.get(
                    "execution_writer_boundary"
                ),
                goal_continuity=resume_contract.get("goal_continuity"),
                destination_orchestration=resume_contract.get(
                    "destination_orchestration"
                ),
            )
            continuation_ready = False
        else:
            entry = {
                "entry_action": "RESUME_EXACT_UNFINISHED_STEP",
                "resume_step": resume_contract.get("resume_step"),
                "task_list_sha256": resume_contract.get("task_list_sha256"),
                "additive_deltas_sha256": resume_contract.get("additive_deltas_sha256"),
                "task_or_candidate_cleared": False,
                "pointer_moved": False,
                "destination_orchestration": resume_contract.get(
                    "destination_orchestration"
                ),
                "destination_task_binding": destination.get("destination_task_binding"),
            }
            state_travel_status = "VERIFIED_RESUME_READY"
            wait_state = "WAITING_FOR_HOST_PLAN_ACCEPTANCE"
            next_action = "RESTORE_HOST_PLAN_AND_WAIT_FOR_EXPLICIT_ACCEPTANCE"
            next_action_contract = state_travel_next_action(
                state="WAITING_FOR_HOST_PLAN_ACCEPTANCE",
                command="HOST_UPDATE_PLAN_THEN_WAIT_FOR_EXPLICIT_ACCEPTANCE",
                suggested_next_prompt=str(resume_contract["suggested_next_prompt"]),
                target_surface=str(travel.get("target_surface")),
                display_position="AFTER_STATE_TRAVEL_VERIFICATION",
                stop_and_wait=True,
                task_panel_reactivation=resume_contract.get("panel_reactivation"),
                execution_writer_boundary=resume_contract.get(
                    "execution_writer_boundary"
                ),
                goal_continuity=resume_contract.get("goal_continuity"),
                destination_orchestration=resume_contract.get(
                    "destination_orchestration"
                ),
            )
            continuation_ready = True

        completed_at = utc_now()
        resume_tuple = {
            "handoff_id": handoff_id,
            "destination_host": session.host.value,
            "destination_host_session_id": current_host_session_id,
        }
        consumption_body = {
            "schema": "evidence-lane.state-travel-resume-consumption.v1",
            "status": "CONSUMED_EXACTLY_ONCE",
            "project_id": project_id,
            "session_id": session_id,
            "handoff_id": handoff_id,
            "handoff_sha256": travel.get("handoff_sha256"),
            "resume_tuple": resume_tuple,
            "resume_tuple_sha256": sha256_bytes(canonical_json_bytes(resume_tuple)),
            "destination_task_binding_sha256": cast(
                dict[str, Any],
                destination.get("destination_task_binding") or {},
            ).get("binding_sha256"),
            "resume_invocation_count": 1,
            "host_rebound": True,
            "source_mutated": False,
            "pointer_moved": False,
            "candidate_created": False,
            "candidate_mutated": False,
            "pending_hil_mutated": False,
            "hil_inferred": False,
            "accepted_pv": pointer.accepted_pv,
            "pointer_generation": pointer.generation,
            "candidate_id": session.candidate_id,
            "pending_hil": bool(session.metadata.get("pending_hil")),
            "consumed_at": completed_at,
        }
        resume_consumption_receipt = {
            **consumption_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(consumption_body)),
        }
        verified = {
            **travel,
            "status": state_travel_status,
            "destination_host": session.host.value,
            "destination_host_session_id": current_host_session_id,
            "host_window_opened": True,
            "boot_verified": True,
            "flash_verified": True,
            "flash_authority_version": flash.get("authority_version"),
            "flash_authority_digest": flash.get("authority_digest"),
            "flash_receipt_sha256": flash.get("receipt_sha256"),
            "pointer_verified": True,
            "candidate_verified": bool(candidate_snapshot),
            "plan_lane_verified": True,
            "live_source_verified": source_verified,
            "execution_profile_verified": destination["execution_profile_verified"],
            "destination_task_binding": destination.get("destination_task_binding"),
            "destination_task_binding_verified": destination.get(
                "destination_task_binding_verified"
            ),
            "host_settings_mutated": False,
            "completed_at": completed_at,
            "resume_consumption_receipt": resume_consumption_receipt,
            "wait_state": wait_state,
            "continuation_ready": continuation_ready,
            "native_resume_ready": (
                travel_mode == "UNFINISHED_VERIFIED_WORK" and continuation_ready
            ),
            "host_plan_acceptance_pending": (travel_mode == "UNFINISHED_VERIFIED_WORK"),
            "host_plan_acceptance_is_evidence_lane_hil": False,
            "evidence_plan_verification_pending": (
                travel_mode == "UNFINISHED_VERIFIED_WORK"
            ),
            "goal_start_allowed": False,
            "source_work_allowed": False,
            "continuation_ready_scope": (
                "NATIVE_RESUME_VERIFIED_ONLY_PENDING_HOST_PLAN_ACCEPTANCE_"
                "EVIDENCE_PLAN_AND_GOAL"
                if travel_mode == "UNFINISHED_VERIFIED_WORK"
                else "WAIT_STATE"
            ),
            "next_action_contract": next_action_contract,
        }
        session.metadata["state_travel"] = verified
        session.metadata.setdefault("state_travel_history", []).append(verified)
        self._save(session)
        host_plan_rehydration = None
        if (
            travel_mode == "UNFINISHED_VERIFIED_WORK"
            and resume_contract.get("task_list_source") == "ACTIVE_PLAN_LANE_DERIVED"
        ):
            host_plan_rehydration = self._prepare_host_plan_rehydration(
                project_id,
                session,
                trigger="STATE_TRAVEL_DESTINATION_ENTRY",
                trigger_event_id=str(resume_consumption_receipt["receipt_sha256"]),
                host_goal_active=False,
            )
            require(
                host_plan_rehydration is not None,
                "STATE_TRAVEL_HOST_PLAN_REHYDRATION_REQUIRED",
                "Unfinished State Travel requires an exact physically-final host Plan projection.",
                status="MISMATCH",
            )
            host_plan_rehydration = cast(dict[str, Any], host_plan_rehydration)
            verified["host_plan_rehydration"] = host_plan_rehydration
            session.metadata["state_travel"] = verified
            cast(list[dict[str, Any]], session.metadata["state_travel_history"])[-1] = (
                verified
            )
            session.metadata["last_host_plan_rehydration_receipt_sha256"] = cast(
                dict[str, Any], host_plan_rehydration["receipt"]
            )["receipt_sha256"]
            self._save(session)
        event = ChatLineage(self._lineage_path(project_id, session_id)).append(
            event_type="pv.state_travel.verified",
            visible_payload={
                "handoff_id": handoff_id,
                "travel_mode": travel_mode,
                "origin_state": travel.get("origin_state"),
                "accepted_pv": pointer.accepted_pv,
                "pointer_generation": pointer.generation,
                "manifest_sha256": (
                    validation["manifest_sha256"] if validation else None
                ),
                "package_sha256": (
                    validation["package_sha256"] if validation else None
                ),
                "destination_host": session.host.value,
                "destination_host_session_id": current_host_session_id,
                "boot_verified": True,
                "flash_verified": True,
                "pointer_verified": True,
                "candidate_verified": bool(candidate_snapshot),
                "plan_lane_verified": True,
                "live_source_verified": source_verified,
                "execution_profile_verified": destination["execution_profile_verified"],
                "destination_task_binding_verified": destination.get(
                    "destination_task_binding_verified"
                ),
                "host_plan_acceptance_pending": (
                    travel_mode == "UNFINISHED_VERIFIED_WORK"
                ),
                "next_action": next_action,
            },
            occurred_at=completed_at,
            session_id=session_id,
        )
        return {
            "status": "PASS",
            "state_travel": verified,
            "session": session.as_dict(),
            "entry": entry,
            "pointer": pointer.as_dict(),
            "pointer_verification": {
                "accepted_pv": pointer.accepted_pv,
                "generation": pointer.generation,
                "manifest_sha256": (
                    validation["manifest_sha256"] if validation else None
                ),
                "package_sha256": (
                    validation["package_sha256"] if validation else None
                ),
                "verified": True,
            },
            "wait_state": wait_state,
            "next_action": next_action,
            "suggested_next_prompt": next_action_contract["suggested_next_prompt"],
            "next_action_contract": next_action_contract,
            "host_plan_rehydration": host_plan_rehydration,
            "task_started": False,
            "continuation_ready": continuation_ready,
            "event": event,
        }

    def begin_next_turn(
        self,
        project_id: str,
        session_id: str,
        *,
        continue_same_host: bool = False,
        continuation_reason: str | None = None,
        _state_travel_handoff_id: str | None = None,
    ) -> dict[str, Any]:
        session = self.load(project_id, session_id)
        require(
            session.state in {SessionState.PVN_ACCEPTED, SessionState.PVN1_ACCEPTED},
            "NEXT_TURN_STATE_INVALID",
            "A follow-up turn begins only from a newly accepted PV.",
            status="BLOCKED",
            state=session.state.value,
        )
        travel = session.metadata.get("state_travel")
        same_host_travel: dict[str, Any] | None = None
        if isinstance(travel, dict) and travel.get("status") == "PREPARED":
            handoff_matches = bool(_state_travel_handoff_id) and (
                _state_travel_handoff_id == travel.get("handoff_id")
            )
            if not handoff_matches:
                require(
                    continue_same_host
                    and continuation_reason == "EXPLICIT_USER_CONTINUATION",
                    "STATE_TRAVEL_RESUME_REQUIRED",
                    "A prepared accepted-PV handoff remains parked unless State "
                    "Travel resumes it or the user explicitly continues in the "
                    "same host.",
                    status="BLOCKED",
                    target_surface=travel.get("target_surface"),
                    allowed_same_host_reason="EXPLICIT_USER_CONTINUATION",
                )
                current_host_session_id = str(
                    session.metadata.get("current_host_session_id") or ""
                )
                origin_host_session_id = str(travel.get("origin_host_session_id") or "")
                require(
                    bool(current_host_session_id)
                    and current_host_session_id == origin_host_session_id,
                    "STATE_TRAVEL_SAME_HOST_CONTINUATION_MISMATCH",
                    "Same-host continuation cannot bypass a prepared handoff after "
                    "the governed host session has changed.",
                    status="BLOCKED",
                    current_host_session_id=current_host_session_id or None,
                    origin_host_session_id=origin_host_session_id or None,
                )
                same_host_travel = travel
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
        state_travel_disposition: dict[str, Any] | None = None
        if same_host_travel is not None:
            prepared_receipt = dict(same_host_travel)
            session.metadata.setdefault("state_travel_history", []).append(
                prepared_receipt
            )
            state_travel_disposition = {
                "schema": "evidence-lane.state-travel-disposition.v1",
                "status": "SUPERSEDED_BY_SAME_HOST_CONTINUATION",
                "handoff_id": prepared_receipt.get("handoff_id"),
                "handoff_sha256": prepared_receipt.get("handoff_sha256"),
                "accepted_pv": prepared_receipt.get("accepted_pv"),
                "pointer_generation": prepared_receipt.get("pointer_generation"),
                "continuation_reason": continuation_reason,
                "host_session_id": session.metadata.get("current_host_session_id"),
                "pointer_moved": False,
                "state_travel_consumed": False,
                "superseded_at": utc_now(),
            }
            session.metadata["state_travel"] = state_travel_disposition
        session.metadata.setdefault("entry_history", []).append(entry_receipt)
        session.state = transition(
            session.state,
            LifecycleEvent.BEGIN_NEXT_TURN,
            SessionState.PVN1_ENTRY,
        )
        self._save(session)
        disposition_event: dict[str, Any] | None = None
        if state_travel_disposition is not None:
            disposition_event = ChatLineage(
                self._lineage_path(project_id, session_id)
            ).append(
                event_type="pv.state_travel.superseded_same_host",
                visible_payload=state_travel_disposition,
                occurred_at=str(state_travel_disposition["superseded_at"]),
                session_id=session_id,
            )
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
            "state_travel_disposition": state_travel_disposition,
            "state_travel_disposition_event": disposition_event,
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
