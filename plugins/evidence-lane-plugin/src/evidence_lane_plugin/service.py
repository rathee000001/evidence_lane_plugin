"""Composition root and canonical tool-result contracts."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .constants import LIFECYCLE_RESULT_SCHEMA, TOOL_RESULT_SCHEMA
from .engine import CodePVEngine
from .errors import EvidenceLaneError
from .flash_authority import SessionFlashAuthority
from .models import HostKind, ProjectConfig
from .persistence import (
    GoogleDrivePersistence,
    PVSyncService,
    route_persistence,
)
from .reader import PVReader
from .redaction import redact
from .remote_git import RemoteGitController
from .session import SessionManager
from .store import ProjectStore
from .timeutil import utc_now


class EvidenceLaneService:
    def __init__(
        self,
        *,
        data_root: str | Path | None = None,
        sync_service: PVSyncService | None = None,
    ) -> None:
        package_root = Path(__file__).resolve().parents[2]
        repository_root = Path(__file__).resolve().parents[4]
        configured_root = (
            Path(data_root)
            if data_root
            else Path(
                os.environ.get("EVIDENCE_LANE_DATA_ROOT")
                or os.environ.get("PLUGIN_DATA")
                or package_root / ".plugin-data"
            )
        )
        self.store = ProjectStore(configured_root)
        self.flash_authority = SessionFlashAuthority(data_root=configured_root)
        self.engine = CodePVEngine(
            store=self.store,
            source_repository_root=repository_root,
            package_source_root=Path(__file__).resolve().parent,
        )
        self.sessions = SessionManager(self.store, self.engine)
        self.reader = PVReader(self.store)
        self.remote_git = RemoteGitController(self.store)
        self.sync_service = sync_service or self._environment_sync_service()

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
                "engine": "evidence-lane-private-code-engine",
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
                "engine": "evidence-lane-private-code-engine",
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
        report["installation"] = installation
        report["session_flash"] = flash
        report["checks"]["session_flash_bundle"] = flash["status"] == "PASS"
        report["status"] = "PASS" if all(report["checks"].values()) else "FAIL"
        report["warnings"] = flash["warnings"]
        report["google_drive_configured"] = self.sync_service is not None
        return report

    def session_flash_status(self) -> dict[str, Any]:
        return self.flash_authority.status()

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
                    persistence_mode="governed_by_host",
                    sensitivity=sensitivity.upper(),
                )
            ),
        }

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
    ) -> dict[str, Any]:
        flash = self.flash_authority.ensure_flashed()
        route = route_persistence(host, ephemeral=ephemeral)
        if route.durable_required and self.sync_service is None:
            raise EvidenceLaneError(
                "DURABLE_PERSISTENCE_NOT_CONFIGURED",
                "This remote or ephemeral host requires the user-owned Google Drive persistence boundary.",
                status="BLOCKED",
                details={"mode": route.mode, "reason": route.reason},
            )
        result = self.sessions.boot(
            project_id=project_id,
            user_id=user_id,
            workspace_id=workspace_id,
            host=HostKind(host),
            agent_id=agent_id,
            sandbox_id=sandbox_id,
            persistence_mode=route.mode,
            ephemeral=ephemeral,
            runtime_context=runtime_context,
            flash=flash,
        )
        result["persistence_route"] = {
            "mode": route.mode,
            "reason": route.reason,
            "durable_required": route.durable_required,
        }
        return result

    def build_initial(self, project_id: str, session_id: str) -> dict[str, Any]:
        result = self.sessions.build_initial_entry(project_id, session_id)
        session = self.sessions.load(project_id, session_id)
        if session.metadata["persistence_mode"] == "google_drive":
            sync = self._required_sync_service()
            result["durable_persistence"] = sync.sync_pv(
                project_id,
                result["candidate"]["candidate_id"],
                category="candidates",
            )
        return result

    def refresh(self, project_id: str, session_id: str) -> dict[str, Any]:
        result = self.sessions.refresh_exit(project_id, session_id)
        session = self.sessions.load(project_id, session_id)
        if session.metadata["persistence_mode"] == "google_drive":
            sync = self._required_sync_service()
            result["durable_persistence"] = sync.sync_pv(
                project_id,
                result["candidate"]["candidate_id"],
                category="candidates",
            )
        return result

    def decide(self, project_id: str, session_id: str, **kwargs: Any) -> dict[str, Any]:
        result = self.sessions.decide(project_id, session_id, **kwargs)
        session = self.sessions.load(project_id, session_id)
        if session.metadata["persistence_mode"] == "google_drive":
            sync = self._required_sync_service()
            result["decision_persistence"] = sync.sync_receipt(
                project_id, result["decision"]
            )
            if result["pointer_advanced"]:
                result["accepted_persistence"] = sync.sync_pv(
                    project_id,
                    result["pointer"]["accepted_pv"],
                    category="accepted",
                )
        return result

    def _required_sync_service(self) -> PVSyncService:
        if self.sync_service is None:
            raise EvidenceLaneError(
                "DURABLE_PERSISTENCE_NOT_CONFIGURED",
                "The governed session requires user-owned Google Drive persistence.",
                status="BLOCKED",
            )
        return self.sync_service
