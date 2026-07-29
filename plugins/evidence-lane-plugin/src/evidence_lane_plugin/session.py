"""Persistent governed session and explicit six-outcome HIL state machine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from .constants import ENGINE_VERSION
from .engine import CodePVEngine
from .errors import EvidenceLaneError, require
from .freshness import evaluate_freshness
from .git_adapter import identity_json, inspect_repository
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes
from .ids import prefixed_id
from .ingest import iter_source_files
from .lanes import LaneRegistryError, resolve_lane_id
from .lineage import ChatLineage
from .models import (
    HilDecision,
    HostKind,
    SessionRecord,
    SessionState,
    TaskClass,
    TaskContract,
    normalize_host_kind,
)
from .prompt_index import PromptIndex, is_prompt_reference
from .pv_package import validate_pv_package
from .redaction import redact
from .state_law import LifecycleEvent, transition
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
        host_session_id: str | None = None,
        client_can_edit_source: bool | None = None,
        server_has_durable_filesystem: bool | None = None,
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
            accepted = self.store.accepted_path(project_id, pointer.accepted_pv)
            validation = validate_pv_package(accepted)
            entry_validation = validation
            project_identity = json.loads(
                (accepted / "project_identity.json").read_text(encoding="utf-8")
            )
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
                "The current repository identity is not the repository bound to the "
                "accepted PV.",
                status="MISMATCH",
                mismatches=mismatches,
            )
            require(
                validation["manifest_sha256"] == pointer.accepted_manifest_sha256,
                "ACCEPTED_POINTER_HASH_MISMATCH",
                "The active pointer does not match the accepted PV manifest.",
                status="MISMATCH",
            )
            entry_freshness = evaluate_freshness(self.store, project_id, accepted)
        session_id = prefixed_id("session")
        now = utc_now()
        safe_context = redact(runtime_context or {})
        context_hash = sha256_bytes(canonical_json_bytes(safe_context))
        exact_host_session_id = str(host_session_id or "").strip() or None
        source_edit_authority = self._source_edit_authority(
            host_kind,
            client_can_edit_source,
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
                "installation_state": installation["state"],
                "flash_authority_version": flash["authority_version"],
                "flash_authority_digest": flash["authority_digest"],
                "flash_action": flash["flash_action"],
                "flash_context_stored_in_pv": False,
                "persistence_mode": persistence_mode,
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
                if entry_freshness["state"] == "FRESH"
                else "REVIEW_STALE_ACCEPTED_ENTRY_BEFORE_MUTATING_TASK"
            ),
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
        ephemeral: bool,
        client_can_edit_source: bool | None = None,
        server_has_durable_filesystem: bool | None = None,
        runtime_context: dict[str, Any] | None = None,
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
                "pointer_moved": False,
            },
            occurred_at=now,
            session_id=session.session_id,
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
        return {
            "status": "PASS",
            "resumed": True,
            "session": session.as_dict(),
            "pointer": pointer.as_dict(),
            "entry_action": entry_action,
            "event": event,
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
            and (pointer.accepted_pv is not None or pending_state),
            "TASK_CLASSIFICATION_STATE_INVALID",
            "A task requires an accepted entry PV, except for the exact stored "
            "follow-up to an unaccepted initial PV1 candidate.",
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
        if backlog_task_id:
            claimed = self.store.claim_backlog_task(
                project_id,
                backlog_task_id=backlog_task_id,
                session_id=session_id,
                contract=task.as_dict(),
            )
            session.metadata["active_backlog_task_id"] = claimed["task_id"]
        mutating_classes = {
            TaskClass.MODIFY_CODE,
            TaskClass.FIX_BUG,
            TaskClass.ADD_BOUNDED_FEATURE,
            TaskClass.PREPARE_PATCH,
        }
        current_freshness = (
            evaluate_freshness(
                self.store,
                project_id,
                self.store.accepted_path(project_id, cast(str, pointer.accepted_pv)),
            )
            if pointer.accepted_pv is not None
            else {
                "state": "PENDING_INITIAL_CANDIDATE",
                "reason": (
                    "The exact HIL follow-up is bound to an unaccepted PV1 "
                    "candidate; no accepted pointer exists."
                ),
            }
        )
        session.metadata["current_accepted_freshness"] = current_freshness
        require(
            pending_state
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
        session.task = task.as_dict()
        session.candidate_id = None
        target_state = (
            SessionState.AWAITING_USER_APPLY_COMMIT
            if session.metadata.get("client_source_edit_authority") == "USER_MEDIATED"
            else SessionState.TASK_CLASSIFIED
        )
        session.state = transition(
            session.state,
            LifecycleEvent.CLASSIFY_TASK,
            target_state,
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
            "PV Refresh requires one active classified task.",
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
        session.state = transition(
            session.state,
            LifecycleEvent.BEGIN_EXIT,
            SessionState.EXIT_BUILDING,
        )
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
            session.metadata["current_accepted_freshness"] = evaluate_freshness(
                self.store,
                project_id,
                self.store.accepted_path(project_id, cast(str, after.accepted_pv)),
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
        backlog_task_id = session.metadata.get("active_backlog_task_id")
        if backlog_task_id:
            backlog_outcome = self.store.record_backlog_outcome(
                project_id,
                backlog_task_id=cast(str, backlog_task_id),
                session_id=session_id,
                decision=outcome.value,
                candidate_id=candidate_id,
                accepted_pv=self.store.pointer(project_id).accepted_pv,
            )
            session.metadata.pop("active_backlog_task_id", None)
            session.metadata["last_backlog_outcome"] = {
                "task_id": backlog_outcome["task_id"],
                "status": backlog_outcome["status"],
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
        require(
            pointer.accepted_pv is not None,
            "NEXT_TURN_ACCEPTED_PV_MISSING",
            "Direct handoff requires the newly accepted immutable PV.",
            status="MISMATCH",
        )
        accepted_path = self.store.accepted_path(
            project_id,
            cast(str, pointer.accepted_pv),
        )
        validation = validate_pv_package(accepted_path)
        freshness = evaluate_freshness(self.store, project_id, accepted_path)
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
