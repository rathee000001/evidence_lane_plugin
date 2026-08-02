"""Composition root and canonical tool-result contracts."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from .connector_governance import ConnectorGovernance
from .constants import LIFECYCLE_RESULT_SCHEMA, TOOL_RESULT_SCHEMA
from .engine import CodePVEngine
from .engine_identity import identity_repository_root
from .enrollment import enroll_project, sync_selected_branch
from .errors import EvidenceLaneError, require
from .flash_authority import SessionFlashAuthority
from .freshness import evaluate_freshness
from .git_adapter import inspect_repository
from .hashing import sha256_bytes
from .hil_intent import classify_hil_intent
from .ids import prefixed_id
from .lane_reader import LaneReader
from .lineage import ProjectChatLineage
from .models import ProjectConfig, normalize_host_kind
from .next_actions import HIL_CHOICES, HIL_SUGGESTED_PROMPT
from .operating_modes import classify_operating_modes
from .persistence import (
    GoogleDrivePersistence,
    PersistenceRoute,
    PVSyncService,
    route_persistence,
)
from .prompt_index import PromptIndex
from .pv_package import validate_pv_package
from .reader import PVReader
from .redaction import redact
from .remote_git import RemoteGitController
from .runtime_activation import RuntimeActivation
from .session import SessionManager
from .source_intake import classify_source_intake
from .state_law import transition_catalog
from .storage_selection import StorageSelection
from .store import ProjectStore
from .timeutil import utc_now


class EvidenceLaneService:
    def __init__(
        self,
        *,
        data_root: str | Path | None = None,
        sync_service: PVSyncService | None = None,
    ) -> None:
        repository_root = identity_repository_root(__file__)
        configured_root = (
            Path(data_root)
            if data_root
            else Path(
                os.environ.get("EVIDENCE_LANE_DATA_ROOT")
                or os.environ.get("PLUGIN_DATA")
                or Path.home() / "EvidenceLanePV"
            )
        )
        self.store = ProjectStore(configured_root)
        self.flash_authority = SessionFlashAuthority(data_root=configured_root)
        self.runtime_activation = RuntimeActivation(configured_root)
        self.storage_selection = StorageSelection(self.store)
        self.engine = CodePVEngine(
            store=self.store,
            source_repository_root=repository_root,
            package_source_root=Path(__file__).resolve().parent,
        )
        self.sessions = SessionManager(
            self.store,
            self.engine,
            runtime_activation=self.runtime_activation,
        )
        self.reader = PVReader(self.store)
        self.lane_reader = LaneReader(self.store)
        self.remote_git = RemoteGitController(self.store)
        self.sync_service = sync_service or self._environment_sync_service()

    def storage_connector_inspect(
        self,
        project_id: str,
        *,
        host: str | None = None,
        ephemeral: bool = False,
        server_has_durable_filesystem: bool | None = None,
    ) -> dict[str, Any]:
        selection = self.storage_selection.inspect(project_id)
        result: dict[str, Any] = {
            **selection,
            "configured_runtime_connector_available": bool(
                self.sync_service is not None
                and self.sync_service.runtime_state_capable
            ),
        }
        if host:
            route, _ = self._selected_persistence_route(
                project_id,
                host=host,
                ephemeral=ephemeral,
                server_has_durable_filesystem=server_has_durable_filesystem,
            )
            result["effective_route"] = {
                "mode": route.mode,
                "reason": route.reason,
                "durable_required": route.durable_required,
                "server_filesystem": route.server_filesystem,
                "host_connector_role": route.host_connector_role,
            }
        return result

    def storage_connector_select(
        self, project_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self.storage_selection.select(project_id, **kwargs)

    def _selected_persistence_route(
        self,
        project_id: str,
        *,
        host: str,
        ephemeral: bool,
        server_has_durable_filesystem: bool | None,
    ) -> tuple[PersistenceRoute, dict[str, Any]]:
        host_kind = normalize_host_kind(host)
        automatic = route_persistence(
            host_kind,
            ephemeral=ephemeral,
            server_has_durable_filesystem=server_has_durable_filesystem,
        )
        selection = self.storage_selection.inspect(project_id)
        if selection["mode"] == "AUTO":
            return automatic, selection
        if selection["mode"] == "LOCAL_SQLITE":
            require(
                not ephemeral and automatic.server_filesystem == "DURABLE",
                "LOCAL_SQLITE_STORAGE_UNAVAILABLE",
                "The selected local SQLite authority is unavailable on this host.",
                status="BLOCKED",
            )
            return PersistenceRoute(
                mode="local",
                reason="the project explicitly selected its durable local SQLite authority",
                durable_required=False,
                server_filesystem="DURABLE",
                host_connector_role="OPTIONAL_VERIFIED_MIRROR",
            ), selection
        return PersistenceRoute(
            mode="configured_durable_connector",
            reason=(
                "the project explicitly selected the configured transactional "
                f"connector {selection['connector_id']}"
            ),
            durable_required=True,
            server_filesystem=automatic.server_filesystem,
            host_connector_role="PRIMARY_TRANSACTIONAL_RUNTIME_AUTHORITY",
        ), selection

    def _environment_sync_service(self) -> PVSyncService | None:
        token = os.environ.get("EVIDENCE_LANE_GOOGLE_DRIVE_ACCESS_TOKEN", "")
        folder = os.environ.get("EVIDENCE_LANE_GOOGLE_DRIVE_FOLDER_ID", "")
        if not token and not folder:
            return None
        backend = GoogleDrivePersistence(
            access_token=token,
            parent_folder_id=folder,
        )
        return PVSyncService(
            store=self.store,
            backend=backend,
            drive_encryption_key=os.environ.get("EVIDENCE_LANE_DRIVE_ENCRYPTION_KEY"),
        )

    @staticmethod
    def _result(
        tool: str,
        data: dict[str, Any],
        *,
        lifecycle: bool = False,
    ) -> dict[str, Any]:
        status = str(data.get("status", "PASS"))
        return {
            "schema": LIFECYCLE_RESULT_SCHEMA if lifecycle else TOOL_RESULT_SCHEMA,
            "tool": tool,
            "status": status,
            "data": redact(data),
            "warnings": redact(data.get("warnings", [])),
            "error": None,
            "provenance": {
                "engine": "evidence-lane-universal-pv-engine",
                "generated_at": utc_now(),
                "fabricated_evidence": False,
            },
        }

    @staticmethod
    def _error(
        tool: str,
        error: Exception,
        *,
        lifecycle: bool = False,
    ) -> dict[str, Any]:
        if isinstance(error, EvidenceLaneError):
            payload = error.as_dict()
        else:
            payload = {
                "code": "UNEXPECTED_INTERNAL_ERROR",
                "message": "The operation failed inside the governed plugin boundary.",
                "status": "FAIL",
                "details": {
                    "type": type(error).__name__,
                    "trace_id": __import__("uuid").uuid4().hex,
                },
            }
        return {
            "schema": LIFECYCLE_RESULT_SCHEMA if lifecycle else TOOL_RESULT_SCHEMA,
            "tool": tool,
            "status": payload["status"],
            "data": None,
            "warnings": [],
            "error": redact(payload),
            "provenance": {
                "engine": "evidence-lane-universal-pv-engine",
                "generated_at": utc_now(),
                "fabricated_evidence": False,
            },
        }

    def invoke(
        self,
        tool: str,
        function: Callable[..., dict[str, Any]],
        /,
        *args: Any,
        lifecycle: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            return self._result(
                tool,
                function(*args, **kwargs),
                lifecycle=lifecycle,
            )
        # MCP tools must return the canonical fail-closed envelope even when an
        # unexpected library or OS exception crosses the private-engine boundary.
        except Exception as error:  # noqa: BLE001
            return self._error(tool, error, lifecycle=lifecycle)

    def doctor(self) -> dict[str, Any]:
        installation = self.sessions.installation_status()
        report = self.engine.doctor()
        flash = self.flash_authority.status()
        runtime_activation = self.runtime_activation.status()
        report["installation"] = installation
        report["session_flash"] = flash
        report["runtime_activation"] = runtime_activation
        report["checks"]["session_flash_bundle"] = flash["status"] == "PASS"
        report["status"] = "PASS" if all(report["checks"].values()) else "FAIL"
        report["warnings"] = flash["warnings"]
        report["google_drive_configured"] = bool(
            self.sync_service is not None
            and isinstance(self.sync_service.backend, GoogleDrivePersistence)
        )
        report["durable_runtime_connector_configured"] = bool(
            self.sync_service is not None and self.sync_service.runtime_state_capable
        )
        report["google_drive"] = {
            "host_connector_dependency": ("CAPABILITY_ROUTED_NOT_GLOBALLY_REQUIRED"),
            "connector_id": "connector_5f3c8c41a1e54ad7a76272c89e2554fa",
            "oauth_route": "NORMAL_HOST_CONNECTOR",
            "connector_token_exposed_to_plugin_mcp": False,  # nosec B105
            "connector_connection_state": (
                "HOST_OAUTH_OPTIONAL_UNTIL_PERSISTENCE_ROUTE_SELECTS_DRIVE"
            ),
            "connector_role": "OAUTH_ONBOARDING_AND_VERIFIED_MIRROR",
            "direct_server_backend_configured": bool(
                self.sync_service is not None
                and self.sync_service.runtime_state_capable
            ),
            "direct_server_backend_role": (
                "OPTIONAL_SEALED_ARTIFACT_MIRROR_NOT_PRIMARY_RUNTIME_AUTHORITY"
            ),
        }
        return report

    def session_flash_status(self) -> dict[str, Any]:
        return {
            **self.flash_authority.status(),
            "runtime_activation": self.runtime_activation.status(),
        }

    def runtime_activation_status(self) -> dict[str, Any]:
        return {"status": "PASS", **self.runtime_activation.status()}

    def transition_law(self) -> dict[str, Any]:
        return {"status": "PASS", **transition_catalog()}

    def lane_catalog(self) -> dict[str, Any]:
        return self.lane_reader.lane_catalog()

    def _connector_governance(self, project_id: str) -> ConnectorGovernance:
        self.store.config(project_id)
        return ConnectorGovernance(
            self.store.project_root(project_id) / "connector_brain.sqlite"
        )

    def connector_plugin_register(
        self, project_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._connector_governance(project_id).register(**kwargs)

    def connector_plugin_drop(self, project_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._connector_governance(project_id).drop(**kwargs)

    def connector_plugin_catalog(self, project_id: str) -> dict[str, Any]:
        return self._connector_governance(project_id).catalog()

    def connector_plugin_settings(
        self, project_id: str, *, host_profile: str
    ) -> dict[str, Any]:
        return self._connector_governance(project_id).settings(
            host_profile=host_profile
        )

    def connector_plugin_route(
        self,
        project_id: str,
        *,
        capability: str,
        canonical_lane_id: str | None = None,
        host_profile: str = "CODEX",
    ) -> dict[str, Any]:
        return self._connector_governance(project_id).route(
            capability=capability,
            canonical_lane_id=canonical_lane_id,
            host_profile=host_profile,
        )

    def classify_hil_intent(
        self,
        project_id: str,
        session_id: str,
        utterance: str,
        *,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        session = self.sessions.load(project_id, session_id)
        pending_hil = session.state.value in {"PV1_CANDIDATE", "PVN1_CANDIDATE"}
        result = classify_hil_intent(
            utterance,
            candidate_id=session.candidate_id,
            pending_hil=pending_hil,
        )
        receipt = self.sessions.record_hil_intent(
            project_id,
            session_id,
            classification={**result, "visible_utterance": utterance},
            event_id=event_id,
        )
        result["chat_lineage"] = {
            "append_status": "APPENDED",
            "event_id": receipt["event"]["event_id"],
            "event_sha256": receipt["event"]["event_sha256"],
        }
        return result

    def source_intake(
        self,
        project_id: str,
        sources: list[str],
        *,
        overrides: dict[str, str] | None = None,
        session_id: str | None = None,
        git_mode: str = "AUTO",
    ) -> dict[str, Any]:
        """Classify ordered sources through one generalized public control."""

        config = self.store.config(project_id)
        code_lane = config.source_lane
        if code_lane not in {"github_code", "local_code"}:
            try:
                repository = inspect_repository(config.repository_path)
            except EvidenceLaneError:
                code_lane = "local_code"
            else:
                code_lane = (
                    "github_code" if repository.provider == "github" else "local_code"
                )
        result = classify_source_intake(
            sources,
            code_mode=code_lane,
            overrides=overrides,
            git_mode=git_mode,
        )
        active_session_id = session_id.strip() if session_id else ""
        if not active_session_id:
            active_path = self.store.project_root(project_id) / "active_session.json"
            if active_path.is_file():
                active_session_id = str(
                    json.loads(active_path.read_text(encoding="utf-8")).get(
                        "session_id"
                    )
                    or ""
                )
        if active_session_id:
            receipt = self.sessions.record_source_intake_classification(
                project_id,
                active_session_id,
                classification=result,
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": receipt["event"]["event_id"],
            }
            result["prior_lifecycle_state"] = receipt["lifecycle_state_unchanged"]
            result["pointer"] = receipt["pointer"]
        else:
            result["chat_lineage"] = {"append_status": "NO_ACTIVE_SESSION"}
            result["prior_lifecycle_state"] = "NO_ACTIVE_SESSION"
        result["next_action"] = "RETURN_TO_SOURCE_INTAKE_OR_PRIOR_LIFECYCLE_POSITION"
        return result

    def classify_mode(
        self,
        project_id: str,
        request: str,
        *,
        explicit_modes: list[str] | None = None,
        session_id: str | None = None,
        custom_modes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Classify an ENV15 mode intersection and its canonical lanes."""

        config = self.store.config(project_id)
        repository = inspect_repository(config.repository_path)
        code_lane = config.source_lane
        if code_lane not in {"github_code", "local_code"}:
            code_lane = (
                "github_code" if repository.provider == "github" else "local_code"
            )
        result = classify_operating_modes(
            request,
            explicit_modes=explicit_modes,
            code_lane=code_lane,
            custom_modes=custom_modes,
        )
        active_session_id = session_id.strip() if session_id else ""
        if not active_session_id:
            active_path = self.store.project_root(project_id) / "active_session.json"
            if active_path.is_file():
                active = json.loads(active_path.read_text(encoding="utf-8"))
                active_session_id = str(active.get("session_id") or "")
        if active_session_id:
            receipt = self.sessions.record_mode_classification(
                project_id,
                active_session_id,
                classification=result,
            )
            result["chat_lineage"]["append_status"] = "APPENDED"
            result["chat_lineage"]["event_id"] = receipt["event"]["event_id"]
            result["prior_lifecycle_state"] = receipt["lifecycle_state_unchanged"]
            result["pointer"] = receipt["pointer"]
            if "PL" in {item["id"] for item in result["selected_modes"]}:
                result["plan_runtime"] = self.store.record_planning_mode(
                    project_id,
                    source_event_id=receipt["event"]["event_id"],
                    session_id=active_session_id,
                    request_sha256=sha256_bytes(
                        str(result.get("request", "")).encode("utf-8")
                    ),
                    selected_mode_ids=[item["id"] for item in result["selected_modes"]],
                    mode_intersection=result["mode_intersection"],
                    canonical_lanes=result["canonical_lanes"],
                    lifecycle_state=receipt["lifecycle_state_unchanged"],
                    pointer_generation=int(receipt["pointer"]["generation"]),
                )
            else:
                result["plan_runtime"] = {
                    "status": "NOT_SELECTED",
                    "append_status": "NOT_APPLICABLE",
                    "canonical_plan_sector_mutated": False,
                    "projection_role": "DERIVED_CONTROL_PLANE_INDEX",
                }
        else:
            result["chat_lineage"]["append_status"] = "NO_ACTIVE_SESSION"
            result["prior_lifecycle_state"] = "NO_ACTIVE_SESSION"
            result["plan_runtime"] = {
                "status": "NO_ACTIVE_SESSION",
                "append_status": "NOT_APPENDED",
                "canonical_plan_sector_mutated": False,
                "projection_role": "DERIVED_CONTROL_PLANE_INDEX",
            }
        result["next_action"] = "RETURN_TO_PRIOR_LIFECYCLE_POSITION"
        return result

    def lane_status(
        self,
        project_id: str,
        lane: str,
        *,
        pv_ref: str | None = None,
    ) -> dict[str, Any]:
        result = self.lane_reader.lane_status(project_id, lane, pv_ref=pv_ref)
        if result.get("lane", {}).get("canonical_lane_id") == "plan":
            result["runtime_projection"] = self.store.plan_runtime_status(project_id)
            result["runtime_projection_authority"] = "TASK_BACKLOG_EVENT_LEDGER"
            result["canonical_plan_sector_mutated"] = False
        return result

    def lane_search(
        self,
        project_id: str,
        lane: str,
        query: str,
        *,
        pv_ref: str | None = None,
        limit: int = 20,
        retrieval: str = "hybrid",
    ) -> dict[str, Any]:
        return self.lane_reader.search(
            project_id,
            lane,
            query,
            pv_ref=pv_ref,
            limit=limit,
            retrieval=retrieval,
        )

    def lane_fetch(
        self,
        project_id: str,
        lane: str,
        path: str,
        *,
        pv_ref: str | None = None,
        max_bytes: int = 100_000,
    ) -> dict[str, Any]:
        return self.lane_reader.fetch_source(
            project_id,
            lane,
            path,
            pv_ref=pv_ref,
            max_bytes=max_bytes,
        )

    def configure_lane_routes(
        self,
        project_id: str,
        session_id: str,
        *,
        overrides: dict[str, str],
        granted_by: str,
        grant_id: str | None = None,
    ) -> dict[str, Any]:
        return self.sessions.configure_source_lanes(
            project_id,
            session_id,
            overrides=overrides,
            granted_by=granted_by,
            grant_id=grant_id,
        )

    def status(self, project_id: str) -> dict[str, Any]:
        """Return the durable accepted/candidate/session envelope without mutation."""
        result = self.store.project_status(project_id)
        pointer = self.store.pointer(project_id)
        accepted_history: list[dict[str, Any]] = []
        for pv_id in self.store.accepted_ids(project_id):
            validation = validate_pv_package(
                self.store.accepted_path(project_id, pv_id)
            )
            accepted_history.append(
                {
                    "pv_id": pv_id,
                    "manifest_sha256": validation["manifest_sha256"],
                    "package_sha256": validation["package_sha256"],
                    "current": pointer.accepted_pv == pv_id,
                }
            )
        current_freshness: dict[str, Any] = {
            "state": "NO_ACCEPTED_PV",
            "reason": "PV1 has not been accepted for this project.",
        }
        if pointer.accepted_pv:
            current_freshness = evaluate_freshness(
                self.store,
                project_id,
                self.store.accepted_path(project_id, pointer.accepted_pv),
            )
        active_session: dict[str, Any] | None = None
        active_path = self.store.project_root(project_id) / "active_session.json"
        if active_path.is_file():
            active = json.loads(active_path.read_text(encoding="utf-8"))
            session = self.sessions.load(project_id, active["session_id"])
            if not session.metadata.get("closed_at"):
                active_session = {
                    "session_id": session.session_id,
                    "state": session.state.value,
                    "entry_pv": session.metadata.get("entry_pv"),
                    "accepted_pv": session.accepted_pv,
                    "pointer_generation": session.accepted_pointer_generation,
                    "candidate_id": session.candidate_id,
                    "pending_hil": session.state.value.endswith("_CANDIDATE"),
                    "task_id": (session.task.get("task_id") if session.task else None),
                    "source_state": session.metadata.get("source_state"),
                }
        result.update(
            {
                "status": "PASS",
                "store": str(self.store.root),
                "accepted_history": accepted_history,
                "current_freshness": current_freshness,
                "active_session": active_session,
                "persistent_state_envelope": {
                    "accepted_pv": pointer.accepted_pv,
                    "pointer_generation": pointer.generation,
                    "highest_accepted_ordinal": result["highest_accepted_ordinal"],
                    "next_candidate_pv": result["next_candidate_pv"],
                    "accepted_manifest_sha256": (pointer.accepted_manifest_sha256),
                    "freshness": current_freshness,
                    "pending_candidate": (
                        active_session["candidate_id"] if active_session else None
                    ),
                    "pending_hil": (
                        bool(active_session["pending_hil"]) if active_session else False
                    ),
                },
            }
        )
        return result

    def plan_tasks(
        self,
        project_id: str,
        *,
        tasks: list[dict[str, Any]],
        planned_by: str,
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        return self.store.plan_tasks(
            project_id,
            tasks=tasks,
            planned_by=planned_by,
            plan_id=plan_id or prefixed_id("plan"),
        )

    def task_backlog(self, project_id: str) -> dict[str, Any]:
        return self.store.backlog_status(project_id)

    def transition_task(
        self,
        project_id: str,
        *,
        task_id: str,
        transition_name: str,
        decided_by: str,
        reason: str,
        replacement_task_id: str | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        exact_reason = reason.strip()
        require(
            bool(exact_reason),
            "DELTA_TRANSITION_REASON_REQUIRED",
            "DROP and SUPERSEDE require one visible reason.",
            status="BLOCKED",
        )
        return self.store.transition_backlog_task(
            project_id,
            task_id=task_id,
            transition_name=transition_name,
            decided_by=decided_by,
            reason_sha256=sha256_bytes(exact_reason.encode("utf-8")),
            replacement_task_id=replacement_task_id,
            event_id=event_id,
        )

    def register_project(
        self,
        *,
        project_id: str,
        display_name: str,
        repository_path: str,
        expected_owner: str,
        expected_name: str,
        allowed_branches: list[str],
        sensitivity: str = "PRIVATE",
    ) -> dict[str, Any]:
        return {
            "status": "PASS",
            **self.store.register_project(
                ProjectConfig(
                    project_id=project_id,
                    display_name=display_name,
                    repository_path=str(Path(repository_path).resolve()),
                    expected_owner=expected_owner,
                    expected_name=expected_name,
                    allowed_branches=allowed_branches,
                    source_lane="local_code",
                    persistence_mode="governed_by_host",
                    sensitivity=sensitivity.upper(),
                )
            ),
        }

    def enroll_project(
        self,
        *,
        project_id: str,
        display_name: str,
        source: str,
        expected_owner: str,
        expected_name: str,
        branch: str,
        sensitivity: str = "PRIVATE",
    ) -> dict[str, Any]:
        return enroll_project(
            self.store,
            project_id=project_id,
            display_name=display_name,
            source=source,
            expected_owner=expected_owner,
            expected_name=expected_name,
            branch=branch,
            sensitivity=sensitivity,
        )

    def sync_git_source(
        self,
        *,
        project_id: str,
        source: str,
        branch: str,
        session_id: str | None = None,
        expected_commit: str | None = None,
        replace_registered_branch: bool = False,
    ) -> dict[str, Any]:
        active_path = self.store.project_root(project_id) / "active_session.json"
        session = None
        permitted_paths = None
        if active_path.is_file():
            active = json.loads(active_path.read_text(encoding="utf-8"))
            require(
                bool(session_id) and active.get("session_id") == session_id,
                "PROJECT_SYNC_ACTIVE_SESSION_REQUIRED",
                "An active governed session exists; Git sync requires its exact "
                "session ID and bounded task contract.",
                status="BLOCKED",
                active_session_id=active.get("session_id"),
            )
            session = self.sessions.load(project_id, str(session_id))
            require(
                session.task is not None
                and session.state.value
                in {"TASK_CLASSIFIED", "AWAITING_USER_APPLY_COMMIT"},
                "PROJECT_SYNC_TASK_STATE_INVALID",
                "Git sync inside an active session requires one classified task.",
                status="BLOCKED",
                state=session.state.value,
            )
            task_payload = cast(dict[str, Any], session.task)
            permitted_paths = list(task_payload["permitted_paths"])
        result = sync_selected_branch(
            self.store,
            project_id=project_id,
            source=source,
            branch=branch,
            expected_commit=expected_commit,
            permitted_paths=permitted_paths,
            branch_replacement_actor=(
                session.user_id
                if replace_registered_branch and session is not None
                else None
            ),
        )
        if session is not None:
            activity = self.sessions.record_activity(
                project_id,
                session.session_id,
                activity_type=(
                    "git.fast_forward"
                    if result["fast_forward_applied"]
                    else "git.fast_forward.noop"
                ),
                visible_payload={
                    "branch": result["branch"],
                    "source_kind": result["source_kind"],
                    "before_commit": result["before"]["commit_sha"],
                    "after_commit": result["after"]["commit_sha"],
                    "changed_paths": result["changed_paths"],
                    "branch_authority": result["branch_authority"],
                    "remote_write_performed": False,
                    "merge_commit_created": False,
                },
            )
            result["activity"] = activity["event"]
        return result

    def boot_session(
        self,
        *,
        project_id: str,
        user_id: str,
        workspace_id: str,
        host: str,
        agent_id: str,
        sandbox_id: str | None,
        ephemeral: bool,
        runtime_context: dict[str, Any] | None,
        host_session_id: str | None = None,
        client_can_edit_source: bool | None = None,
        server_has_durable_filesystem: bool | None = None,
    ) -> dict[str, Any]:
        project_lineage_entry = ProjectChatLineage(
            self.store.project_root(project_id) / "lineage"
        ).sync()
        flash = self.flash_authority.ensure_flashed()
        host_kind = normalize_host_kind(host)
        route, storage_selection = self._selected_persistence_route(
            project_id,
            host=host_kind.value,
            ephemeral=ephemeral,
            server_has_durable_filesystem=server_has_durable_filesystem,
        )
        if route.durable_required and (
            self.sync_service is None or not self.sync_service.runtime_state_capable
        ):
            raise EvidenceLaneError(
                "DURABLE_RUNTIME_CONNECTOR_NOT_CONFIGURED",
                "This remote or ephemeral host requires a transactional connector for "
                "sessions, backlog, lineage, candidates, receipts, and pointer CAS. "
                "Google Drive may mirror sealed artifacts but is never this primary authority.",
                status="BLOCKED",
                details={"mode": route.mode, "reason": route.reason},
            )
        result = self.sessions.boot(
            project_id=project_id,
            user_id=user_id,
            workspace_id=workspace_id,
            host=host_kind,
            agent_id=agent_id,
            sandbox_id=sandbox_id,
            persistence_mode=route.mode,
            ephemeral=ephemeral,
            runtime_context=runtime_context,
            flash=flash,
            host_session_id=host_session_id,
            client_can_edit_source=client_can_edit_source,
            server_has_durable_filesystem=route.server_filesystem == "DURABLE",
        )
        result["persistence_route"] = {
            "mode": route.mode,
            "reason": route.reason,
            "durable_required": route.durable_required,
            "server_filesystem": route.server_filesystem,
            "host_connector_role": route.host_connector_role,
            "selection": storage_selection,
        }
        result["project_lineage_entry"] = project_lineage_entry
        result["project_lineage"] = ProjectChatLineage(
            self.store.project_root(project_id) / "lineage"
        ).sync()
        return result

    def resume_session(
        self,
        *,
        project_id: str,
        host: str,
        host_session_id: str,
        ephemeral: bool,
        client_can_edit_source: bool | None = None,
        server_has_durable_filesystem: bool | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        project_lineage_entry = ProjectChatLineage(
            self.store.project_root(project_id) / "lineage"
        ).sync()
        flash = self.flash_authority.ensure_flashed()
        host_kind = normalize_host_kind(host)
        route, storage_selection = self._selected_persistence_route(
            project_id,
            host=host_kind.value,
            ephemeral=ephemeral,
            server_has_durable_filesystem=server_has_durable_filesystem,
        )
        if route.durable_required and (
            self.sync_service is None or not self.sync_service.runtime_state_capable
        ):
            raise EvidenceLaneError(
                "DURABLE_RUNTIME_CONNECTOR_NOT_CONFIGURED",
                "This MCP server has no durable filesystem and requires the direct "
                "transactional runtime connector before resume; Drive remains a mirror.",
                status="BLOCKED",
                details={"mode": route.mode, "reason": route.reason},
            )
        result = self.sessions.resume(
            project_id=project_id,
            host=host_kind,
            host_session_id=host_session_id,
            persistence_mode=route.mode,
            ephemeral=ephemeral,
            client_can_edit_source=client_can_edit_source,
            server_has_durable_filesystem=route.server_filesystem == "DURABLE",
            runtime_context=runtime_context,
            flash=flash,
        )
        result["session_flash"] = flash
        result["persistence_route"] = {
            "mode": route.mode,
            "reason": route.reason,
            "durable_required": route.durable_required,
            "server_filesystem": route.server_filesystem,
            "host_connector_role": route.host_connector_role,
            "selection": storage_selection,
        }
        result["project_lineage_entry"] = project_lineage_entry
        result["project_lineage"] = ProjectChatLineage(
            self.store.project_root(project_id) / "lineage"
        ).sync()
        return result

    def prepare_state_travel(
        self,
        project_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Seal the accepted pointer for a fresh host task or chat."""

        return self.sessions.prepare_state_travel(project_id, session_id)

    def resume_state_travel(
        self,
        *,
        project_id: str,
        session_id: str,
        handoff_id: str,
        host: str,
        host_session_id: str,
        ephemeral: bool,
        client_can_edit_source: bool | None = None,
        server_has_durable_filesystem: bool | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Verify Flash, resume in a fresh host window, verify pointer, and wait."""

        active_path = self.store.project_root(project_id) / "active_session.json"
        require(
            active_path.is_file(),
            "STATE_TRAVEL_ACTIVE_SESSION_MISSING",
            "State Travel requires the sealed governed session to remain active.",
            status="BLOCKED",
            project_id=project_id,
        )
        active = json.loads(active_path.read_text(encoding="utf-8"))
        require(
            active.get("session_id") == session_id,
            "STATE_TRAVEL_ACTIVE_SESSION_MISMATCH",
            "The supplied State Travel session is not the active governed session.",
            status="MISMATCH",
            active_session_id=active.get("session_id"),
            supplied_session_id=session_id,
        )
        flash = self.flash_authority.ensure_flashed()
        boot = self.resume_session(
            project_id=project_id,
            host=host,
            host_session_id=host_session_id,
            ephemeral=ephemeral,
            client_can_edit_source=client_can_edit_source,
            server_has_durable_filesystem=server_has_durable_filesystem,
            runtime_context=runtime_context,
        )
        verified = self.sessions.complete_state_travel(
            project_id,
            session_id,
            handoff_id=handoff_id,
            flash=flash,
        )
        return {
            **verified,
            "ordered_entry_verification": [
                "/evi-boot",
                "ATOMIC_BOOT_AND_LOCKED_ENV_UOP_FLASH_VERIFIED",
                "VERIFY_ACCEPTED_POINTER_AND_SEALS",
                "WAITING_FOR_NEXT_USER_COMMAND",
            ],
            "boot": boot,
            "flash": flash,
        }

    def build_initial(self, project_id: str, session_id: str) -> dict[str, Any]:
        result = self.sessions.build_initial_entry(project_id, session_id)
        result["next_action"] = "PRESENT_SIX_WAY_HIL"
        result["suggested_next_prompt"] = HIL_SUGGESTED_PROMPT
        result["next_action_contract"] = result["candidate"]["next_action"]
        result["hil_choices"] = list(HIL_CHOICES)
        session = self.sessions.load(project_id, session_id)
        if session.metadata["persistence_mode"] in {
            "google_drive",
            "configured_durable_connector",
        }:
            sync = self._required_sync_service()
            result["durable_persistence"] = sync.sync_pv(
                project_id,
                result["candidate"]["candidate_id"],
                category="candidates",
            )
        return result

    def refresh(
        self,
        project_id: str,
        session_id: str,
        *,
        batch_task_evidence: list[dict[str, Any]] | None = None,
        batch_completion_confirmation: str | None = None,
    ) -> dict[str, Any]:
        result = self.sessions.refresh_exit(
            project_id,
            session_id,
            batch_task_evidence=batch_task_evidence,
            batch_completion_confirmation=batch_completion_confirmation,
        )
        result["next_action"] = "PRESENT_SIX_WAY_HIL"
        result["suggested_next_prompt"] = HIL_SUGGESTED_PROMPT
        result["next_action_contract"] = result["candidate"]["next_action"]
        result["hil_choices"] = list(HIL_CHOICES)
        session = self.sessions.load(project_id, session_id)
        if session.metadata["persistence_mode"] in {
            "google_drive",
            "configured_durable_connector",
        }:
            sync = self._required_sync_service()
            result["durable_persistence"] = sync.sync_pv(
                project_id,
                result["candidate"]["candidate_id"],
                category="candidates",
            )
        return result

    def complete_task_and_refresh(
        self,
        project_id: str,
        session_id: str,
        *,
        confirmation: str,
        batch_task_evidence: list[dict[str, Any]] | None = None,
        batch_completion_confirmation: str | None = None,
    ) -> dict[str, Any]:
        """Confirm the final host source and seal its exit candidate in one step."""
        if (
            batch_task_evidence is not None
            or batch_completion_confirmation is not None
        ):
            require(
                batch_task_evidence is not None
                and batch_completion_confirmation is not None,
                "BATCH_DELTA_REFRESH_ARGUMENTS_INVALID",
                "Batch completion requires both exact evidence and confirmation.",
                status="BLOCKED",
            )
            exact_batch_evidence = cast(
                list[dict[str, Any]], batch_task_evidence
            )
            exact_batch_confirmation = cast(
                str, batch_completion_confirmation
            )
            self.store.validate_backlog_batch_completion(
                project_id,
                task_evidence=exact_batch_evidence,
                confirmation=exact_batch_confirmation,
            )
        confirmed = self.sessions.confirm_source_update(
            project_id,
            session_id,
            confirmation=confirmation,
        )
        refreshed = self.refresh(
            project_id,
            session_id,
            batch_task_evidence=batch_task_evidence,
            batch_completion_confirmation=batch_completion_confirmation,
        )
        return {
            **refreshed,
            "automatic_refresh": True,
            "user_refresh_command_required": False,
            "source_confirmation": confirmed,
            "next_action": "PRESENT_SIX_WAY_HIL",
            "suggested_next_prompt": HIL_SUGGESTED_PROMPT,
            "next_action_contract": refreshed["candidate"]["next_action"],
            "hil_choices": list(HIL_CHOICES),
        }

    def decide(self, project_id: str, session_id: str, **kwargs: Any) -> dict[str, Any]:
        result = self.sessions.decide(project_id, session_id, **kwargs)
        session = self.sessions.load(project_id, session_id)
        if session.metadata["persistence_mode"] in {
            "google_drive",
            "configured_durable_connector",
        }:
            sync = self._required_sync_service()
            result["decision_persistence"] = sync.sync_receipt(
                project_id, result["decision"]
            )
            if result["pointer_advanced"] or result["pointer_moved"]:
                result["accepted_persistence"] = sync.sync_pv(
                    project_id,
                    result["pointer"]["accepted_pv"],
                    category="accepted",
                )
                result["pointer_persistence"] = sync.sync_pointer(project_id)
        if result["candidate_promoted"]:
            state_travel_handoff = self.sessions.prepare_state_travel(
                project_id,
                session_id,
            )
            result["state_travel_handoff"] = state_travel_handoff
            result["session"] = self.sessions.load(project_id, session_id).as_dict()
        return result

    def record_hil_decision(
        self, project_id: str, session_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Record non-promotion HIL outcomes; APPROVE is exclusive to Fuse."""

        require(
            str(kwargs.get("decision") or "") != "APPROVE",
            "APPROVE_REQUIRES_PV_FUSE",
            "Exact APPROVE may promote only through pv_fuse; continuation, a tool "
            "default, or hil_decide can never imply approval.",
            status="BLOCKED",
        )
        return self.decide(project_id, session_id, **kwargs)

    def fuse(
        self,
        project_id: str,
        session_id: str,
        *,
        approval: str,
        decided_by: str,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        require(
            approval == "APPROVE",
            "PV_FUSE_EXACT_APPROVE_REQUIRED",
            "PV Fuse requires the exact case-sensitive token APPROVE.",
            status="BLOCKED",
            provided=approval,
        )
        result = self.decide(
            project_id,
            session_id,
            decision="APPROVE",
            decided_by=decided_by,
            decision_id=decision_id,
        )
        return {
            "status": "PASS",
            "fused": True,
            "exact_approval": approval,
            "decision": result["decision"],
            "pointer": result["pointer"],
            "state_travel_handoff": result["state_travel_handoff"],
            "candidate_promoted": True,
            "pointer_moved": result["pointer_moved"],
        }

    def prompt_index_status(
        self,
        project_id: str,
        session_id: str,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        session = self.sessions.load(project_id, session_id)
        host_session_id = str(
            session.metadata.get("current_host_session_id") or ""
        ).strip()
        require(
            bool(host_session_id),
            "HOST_SESSION_ID_NOT_BOUND",
            "Run /evi in this host task to bind its SessionStart ID before reading "
            "the prompt index.",
            status="BLOCKED",
        )
        return PromptIndex(self.store.root).status(
            host_session_id=host_session_id,
            project_id=project_id,
            evidence_session_id=session_id,
            limit=limit,
        )

    def rollback(
        self,
        project_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        result = self.sessions.rollback_state(project_id, session_id, **kwargs)
        session = self.sessions.load(project_id, session_id)
        if session.metadata["persistence_mode"] in {
            "google_drive",
            "configured_durable_connector",
        }:
            sync = self._required_sync_service()
            result["decision_persistence"] = sync.sync_receipt(
                project_id, result["decision"]
            )
            if result["pointer_moved"]:
                result["accepted_persistence"] = sync.sync_pv(
                    project_id,
                    result["pointer"]["accepted_pv"],
                    category="accepted",
                )
                result["pointer_persistence"] = sync.sync_pointer(project_id)
        return result

    def _required_sync_service(self) -> PVSyncService:
        if self.sync_service is None:
            raise EvidenceLaneError(
                "DURABLE_PERSISTENCE_NOT_CONFIGURED",
                "The governed session requires user-owned Google Drive persistence.",
                status="BLOCKED",
            )
        return self.sync_service
