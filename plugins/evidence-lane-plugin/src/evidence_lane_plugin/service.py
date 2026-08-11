"""Composition root and canonical tool-result contracts."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from .connector_governance import ConnectorGovernance
from .constants import LIFECYCLE_RESULT_SCHEMA, TOOL_RESULT_SCHEMA
from .custom_source_schema import (
    compile_and_map_custom_source_schema,
    configure_source_intake_schema_pill,
)
from .engine import CodePVEngine
from .engine_identity import identity_repository_root
from .enrollment import enroll_project, sync_selected_branch
from .errors import EvidenceLaneError, require
from .flash_authority import SessionFlashAuthority
from .freshness import evaluate_freshness
from .git_adapter import inspect_repository
from .hashing import canonical_json_bytes, sha256_bytes
from .hil_intent import classify_hil_intent
from .ids import prefixed_id
from .lane_reader import LaneReader
from .lanes import CANONICAL_LANE_IDS, LANE_REGISTRY
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
from .source_git_history import (
    build_registered_git_history,
    build_source_git_commit_impact,
)
from .source_graph import (
    build_registered_source_graph,
    diff_source_graphs,
    source_graph_impact,
)
from .source_identity import register_source_identity_matrix
from .source_intake import classify_source_intake
from .source_sqlite import inspect_registered_sqlite_assets
from .state_law import transition_catalog
from .storage_selection import StorageSelection
from .store import ProjectStore
from .timeutil import utc_now

_STATUS_VALIDATION_WORKERS = 8


def _accepted_lane_projection(
    store: ProjectStore,
    project_id: str,
    accepted_pv: str | None,
) -> dict[str, Any]:
    """Return compact public-safe lane facts from the validated accepted package."""

    if not accepted_pv:
        return {
            "authority": "NO_ACCEPTED_PV",
            "pv_ref": None,
            "canonical_lane_count": len(CANONICAL_LANE_IDS),
            "emitted_lane_count": 0,
            "absent_lane_ids": list(CANONICAL_LANE_IDS),
            "lanes": [
                {
                    "id": lane_id,
                    "label": LANE_REGISTRY[lane_id].display_label,
                    "value": "NO ACCEPTED PV | no lane authority available",
                    "state": "NO_ACCEPTED_PV",
                    "contract_status": "NOT_APPLICABLE",
                    "member_count": 0,
                }
                for lane_id in CANONICAL_LANE_IDS
            ],
        }

    manifest = json.loads(
        (store.accepted_path(project_id, accepted_pv) / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    universal = manifest.get("universal_lanes")
    if not isinstance(universal, dict):
        universal = {}
    emitted = {
        str(lane_id)
        for lane_id in (universal.get("emitted_lane_ids") or [])
        if str(lane_id) in CANONICAL_LANE_IDS
    }
    contracts = universal.get("four_file_contracts")
    if not isinstance(contracts, dict):
        contracts = {}
    lanes: list[dict[str, Any]] = []
    for lane_id in CANONICAL_LANE_IDS:
        contract = contracts.get(lane_id)
        if not isinstance(contract, dict):
            contract = {}
        members = contract.get("members")
        member_count = len(members) if isinstance(members, list) else 0
        state = "EMITTED" if lane_id in emitted else "NOT_EMITTED"
        contract_status = (
            str(contract.get("status") or "UNKNOWN")
            if lane_id in emitted
            else "NOT_APPLICABLE"
        )
        lanes.append(
            {
                "id": lane_id,
                "label": LANE_REGISTRY[lane_id].display_label,
                "value": (
                    f"{state} | {contract_status} | {member_count} sealed files | "
                    f"accepted {accepted_pv}"
                ),
                "state": state,
                "contract_status": contract_status,
                "member_count": member_count,
                "authority": "ACCEPTED_IMMUTABLE_AUTHORITY",
                "pv_ref": accepted_pv,
            }
        )
    absent = [lane_id for lane_id in CANONICAL_LANE_IDS if lane_id not in emitted]
    return {
        "authority": "ACCEPTED_IMMUTABLE_AUTHORITY",
        "pv_ref": accepted_pv,
        "canonical_lane_count": len(CANONICAL_LANE_IDS),
        "manifest_declared_canonical_lane_count": int(
            universal.get("canonical_lane_count") or 0
        ),
        "emitted_lane_count": len(emitted),
        "absent_lane_ids": absent,
        "bundle_sha256": universal.get("bundle_sha256"),
        "topology_status": (
            "PASS" if universal.get("topology_valid") is True else "NOT_PROVEN"
        ),
        "lanes": lanes,
    }


class EvidenceLaneService:
    def __init__(
        self,
        *,
        data_root: str | Path | None = None,
        sync_service: PVSyncService | None = None,
    ) -> None:
        repository_root = identity_repository_root(__file__)
        configured_environment_root = os.environ.get("EVIDENCE_LANE_DATA_ROOT")
        legacy_plugin_root = os.environ.get("PLUGIN_DATA")
        if data_root is not None:
            require(
                bool(os.fspath(data_root).strip()),
                "EVIDENCE_LANE_DATA_ROOT_INVALID",
                "An explicitly configured Evidence Lane data root cannot be empty.",
                status="BLOCKED",
            )
            configured_root = Path(data_root)
            root_source = "EXPLICIT_SERVICE_CONFIGURATION"
        elif configured_environment_root is not None:
            require(
                bool(configured_environment_root.strip()),
                "EVIDENCE_LANE_DATA_ROOT_INVALID",
                "EVIDENCE_LANE_DATA_ROOT cannot be empty when it is configured.",
                status="BLOCKED",
            )
            configured_root = Path(configured_environment_root)
            root_source = "EVIDENCE_LANE_DATA_ROOT"
        elif legacy_plugin_root is not None:
            require(
                bool(legacy_plugin_root.strip()),
                "EVIDENCE_LANE_DATA_ROOT_INVALID",
                "PLUGIN_DATA cannot be empty when it is configured.",
                status="BLOCKED",
            )
            configured_root = Path(legacy_plugin_root)
            root_source = "PLUGIN_DATA_MIGRATION_COMPATIBILITY"
        else:
            configured_root = Path.home() / "EvidenceLanePV"
            root_source = "PLATFORM_PER_USER_DURABLE_DEFAULT"
        self.store = ProjectStore(
            configured_root,
            configuration_source=root_source,
        )
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
            "project_route": self.store.inspect_project_route(project_id),
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
            result["effective_route"] = route.as_dict()
            result["effective_route"]["project_route"] = (
                self.store.inspect_project_route(project_id)
            )
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
        runtime_context: dict[str, Any] | None = None,
    ) -> tuple[PersistenceRoute, dict[str, Any]]:
        host_kind = normalize_host_kind(host)
        automatic = route_persistence(
            host_kind,
            ephemeral=ephemeral,
            server_has_durable_filesystem=server_has_durable_filesystem,
            runtime_context=runtime_context,
        )
        selection = self.storage_selection.inspect(project_id)
        if selection["mode"] == "AUTO":
            return automatic, selection
        if selection["mode"] == "LOCAL_SQLITE":
            require(
                automatic.server_filesystem == "DURABLE",
                "LOCAL_SQLITE_STORAGE_UNAVAILABLE",
                "The selected local SQLite authority is unavailable on this host.",
                status="BLOCKED",
            )
            return replace(
                automatic,
                mode="local",
                reason="the project explicitly selected its durable local SQLite authority",
                durable_required=False,
            ), selection
        return replace(
            automatic,
            mode="configured_durable_connector",
            reason=(
                "the project explicitly selected the configured transactional "
                f"connector {selection['connector_id']}"
            ),
            durable_required=True,
            host_connector_role="PRIMARY_TRANSACTIONAL_RUNTIME_AUTHORITY",
            primary_runtime_authority="CONFIGURED_TRANSACTIONAL_RUNTIME_REQUIRED",
        ), selection

    def _persistence_route_payload(
        self,
        project_id: str,
        route: PersistenceRoute,
    ) -> dict[str, Any]:
        return {
            **route.as_dict(),
            "project_route": self.store.inspect_project_route(project_id),
            "transport_project_binding": "EXPLICIT_PROJECT_ID_PER_PROJECT_SCOPED_TOOL",
            "cross_project_fallback_allowed": False,
        }

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
        report["store_routing"] = self.store.inspect_root()
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
        preferred_plugin_id: str | None = None,
    ) -> dict[str, Any]:
        return self._connector_governance(project_id).route(
            capability=capability,
            canonical_lane_id=canonical_lane_id,
            host_profile=host_profile,
            preferred_plugin_id=preferred_plugin_id,
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
        authority_mode: str = "CLASSIFICATION_ONLY",
        source_assertions: dict[str, dict[str, Any]] | None = None,
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
            authority_mode=authority_mode,
            authority_registry_path=(
                self.store.source_authority_path(project_id)
                if authority_mode.strip().upper() == "GOVERNED_CONTENT_REGISTRY"
                else None
            ),
            source_assertions=source_assertions,
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

    def source_sqlite_inspect(
        self,
        project_id: str,
        batch_id: str,
        *,
        session_id: str | None = None,
        max_embedded_member_bytes: int = 768 * 1024 * 1024,
        exact_count_max_database_bytes: int = 32 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Inspect registered direct and embedded SQLite assets read-only."""

        self.store.config(project_id)
        result = inspect_registered_sqlite_assets(
            self.store.source_authority_path(project_id),
            batch_id,
            max_embedded_member_bytes=max_embedded_member_bytes,
            exact_count_max_database_bytes=exact_count_max_database_bytes,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=f"source_sqlite_{str(result['event_sha256'])[:32].lower()}",
                visible_payload={
                    key: value
                    for key, value in result.items()
                    if key not in {"failure_samples"}
                },
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_custom_schema_compile(
        self,
        project_id: str,
        batch_id: str,
        schema_definition: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Compile and map one declarative Custom Source Schema."""

        self.store.config(project_id)
        result = compile_and_map_custom_source_schema(
            self.store.source_authority_path(project_id),
            batch_id,
            schema_definition,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=(
                    f"source_custom_schema_{str(result['receipt_sha256'])[:32].lower()}"
                ),
                visible_payload={
                    key: value
                    for key, value in result.items()
                    if key not in {"mappings"}
                },
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_intake_schema_configure(
        self,
        project_id: str,
        batch_id: str,
        *,
        operation: str,
        pill_name: str,
        schema_definition: dict[str, Any],
        expected_previous_schema_sha256: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Add or version one governed schema-derived Source Intake pill."""

        self.store.config(project_id)
        result = configure_source_intake_schema_pill(
            self.store.source_authority_path(project_id),
            batch_id,
            operation=operation,
            pill_name=pill_name,
            definition=schema_definition,
            expected_previous_schema_sha256=expected_previous_schema_sha256,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=(
                    "source_intake_schema_"
                    f"{str(result['receipt_sha256'])[:32].lower()}"
                ),
                visible_payload={
                    "operation": result["operation"],
                    "configuration_status": result["configuration_status"],
                    "pill_projection": result["pill_projection"],
                    "receipt_id": result["receipt_id"],
                    "receipt_sha256": result["receipt_sha256"],
                    "candidate_created": False,
                    "pointer_moved": False,
                },
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_identity_register(
        self,
        project_id: str,
        batch_id: str,
        *,
        entities: list[dict[str, Any]],
        profiles: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Append a complete, non-conflating source identity matrix."""

        self.store.config(project_id)
        result = register_source_identity_matrix(
            self.store.source_authority_path(project_id),
            batch_id,
            entities=entities,
            profiles=profiles,
            relations=relations,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=(
                    f"source_identity_{str(result['receipt_sha256'])[:32].lower()}"
                ),
                visible_payload=result,
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_graph_build(
        self,
        project_id: str,
        batch_id: str,
        *,
        occurrence_ordinals: list[int] | None = None,
        member_path_prefixes: list[str] | None = None,
        max_files: int = 25_000,
        max_total_bytes: int = 1024 * 1024 * 1024,
        max_file_bytes: int = 8 * 1024 * 1024,
        max_nodes: int = 500_000,
        max_edges: int = 1_000_000,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Build a bounded provenance-first graph over registered source bytes."""

        self.store.config(project_id)
        result = build_registered_source_graph(
            self.store.source_authority_path(project_id),
            batch_id,
            occurrence_ordinals=occurrence_ordinals,
            member_path_prefixes=member_path_prefixes,
            max_files=max_files,
            max_total_bytes=max_total_bytes,
            max_file_bytes=max_file_bytes,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=f"source_graph_{str(result['receipt_sha256'])[:32].lower()}",
                visible_payload={
                    key: value
                    for key, value in result.items()
                    if key not in {"node_samples", "edge_samples"}
                },
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_graph_diff(
        self,
        project_id: str,
        from_graph_id: str,
        to_graph_id: str,
        *,
        sample_limit: int = 100,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Diff two exact registered source-graph snapshots."""

        self.store.config(project_id)
        result = diff_source_graphs(
            self.store.source_authority_path(project_id),
            from_graph_id,
            to_graph_id,
            sample_limit=sample_limit,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=f"source_graph_diff_{str(result['receipt_sha256'])[:32].lower()}",
                visible_payload={
                    key: value for key, value in result.items() if key != "samples"
                },
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_graph_impact(
        self,
        project_id: str,
        graph_id: str,
        seed_node_ids: list[str],
        *,
        relations: list[str] | None = None,
        direction: str = "UPSTREAM",
        max_depth: int = 3,
        max_nodes: int = 1000,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Traverse one bounded affected subgraph from exact node IDs."""

        self.store.config(project_id)
        result = source_graph_impact(
            self.store.source_authority_path(project_id),
            graph_id,
            seed_node_ids,
            relations=relations,
            direction=direction,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=f"source_graph_impact_{str(result['receipt_sha256'])[:32].lower()}",
                visible_payload={
                    key: value for key, value in result.items() if key != "nodes"
                },
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_git_history_build(
        self,
        project_id: str,
        batch_id: str,
        occurrence_ordinal: int,
        *,
        max_refs: int = 20_000,
        max_commits: int = 100_000,
        max_objects: int = 2_000_000,
        max_tree_entries: int = 5_000_000,
        max_file_changes: int = 2_000_000,
        max_hunks: int = 2_000_000,
        max_changed_lines: int = 5_000_000,
        max_patch_bytes: int = 2 * 1024 * 1024 * 1024,
        max_single_object_bytes: int = 1024 * 1024 * 1024,
        max_total_object_bytes: int = 8 * 1024 * 1024 * 1024,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Seal full, bounded, read-only Git evidence for one source occurrence."""

        self.store.config(project_id)
        result = build_registered_git_history(
            self.store.source_authority_path(project_id),
            batch_id,
            occurrence_ordinal,
            max_refs=max_refs,
            max_commits=max_commits,
            max_objects=max_objects,
            max_tree_entries=max_tree_entries,
            max_file_changes=max_file_changes,
            max_hunks=max_hunks,
            max_changed_lines=max_changed_lines,
            max_patch_bytes=max_patch_bytes,
            max_single_object_bytes=max_single_object_bytes,
            max_total_object_bytes=max_total_object_bytes,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=(
                    f"source_git_history_{str(result['receipt_sha256'])[:32].lower()}"
                ),
                visible_payload=result,
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_git_commit_impact(
        self,
        project_id: str,
        snapshot_id: str,
        graph_id: str,
        commit_sha: str,
        *,
        parent_ordinal: int = 0,
        relations: list[str] | None = None,
        direction: str = "UPSTREAM",
        max_depth: int = 3,
        max_nodes: int = 1000,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Bind an exact Git parent-diff to a bounded semantic impact graph."""

        self.store.config(project_id)
        result = build_source_git_commit_impact(
            self.store.source_authority_path(project_id),
            snapshot_id,
            graph_id,
            commit_sha,
            parent_ordinal=parent_ordinal,
            relations=relations,
            direction=direction,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=(
                    f"source_git_impact_{str(result['receipt_sha256'])[:32].lower()}"
                ),
                visible_payload={
                    key: value
                    for key, value in result.items()
                    if key not in {"changed_paths", "mapped_paths", "unmapped_paths"}
                },
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
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
            result["mode_binding"] = receipt["mode_binding"]
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
        accepted_ids = self.store.accepted_ids(project_id)
        accepted_validations: list[dict[str, Any]] = []
        if accepted_ids:
            # Accepted PVs are independent immutable directories. Validate them
            # concurrently so status keeps full checksum/tamper detection without
            # serially re-reading an entire multi-generation history.
            with ThreadPoolExecutor(
                max_workers=min(_STATUS_VALIDATION_WORKERS, len(accepted_ids))
            ) as executor:
                accepted_validations = list(
                    executor.map(
                        lambda pv_id: validate_pv_package(
                            self.store.accepted_path(project_id, pv_id),
                            require_promotable=False,
                        ),
                        accepted_ids,
                    )
                )
        accepted_history: list[dict[str, Any]] = []
        for pv_id, validation in zip(
            accepted_ids,
            accepted_validations,
            strict=True,
        ):
            is_current = pointer.accepted_pv == pv_id
            # Current topology rules qualify the active authority only. Older
            # accepted PVs remain immutable, checksum-validated evidence even
            # when their topology predates the current promotability contract.
            lane_validation = validation["lanes"]
            accepted_history.append(
                {
                    "pv_id": pv_id,
                    "manifest_sha256": validation["manifest_sha256"],
                    "package_sha256": validation["package_sha256"],
                    "current": is_current,
                    "validation_scope": (
                        "ACCEPTED_IMMUTABLE_AUTHORITY"
                        if is_current
                        else "HISTORICAL_EVIDENCE"
                    ),
                    "integrity_validated": True,
                    "promotability_required": False,
                    "promotability_enforced": False,
                    "promotable": validation["promotable"],
                    "promotable_under_current_rules": validation["promotable"],
                    "lane_topology_status": lane_validation["status"],
                    "lane_topology_valid": lane_validation["valid"],
                    "historical_compatibility_path": bool(
                        not validation["promotable"]
                    ),
                    "successor_candidate_must_pass_current_rules": True,
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
                    "host": session.host.value,
                    "persistence_route": session.metadata.get("persistence_route"),
                }
        storage_selection = self.storage_selection.inspect(project_id)
        project_route = {
            **self.store.inspect_project_route(project_id),
            "storage_mode": storage_selection["mode"],
            "storage_connector_id": storage_selection.get("connector_id"),
            "google_drive_primary_runtime_allowed": False,
        }
        if active_session:
            active_route = active_session.get("persistence_route") or {}
            project_route["active_host_profile"] = active_route.get("host_profile")
            project_route["active_server_filesystem"] = active_route.get(
                "server_filesystem"
            )
        lane_projection = _accepted_lane_projection(
            self.store,
            project_id,
            pointer.accepted_pv,
        )
        result.update(
            {
                "status": "PASS",
                "store": str(self.store.root),
                "project_route": project_route,
                "storage_selection": storage_selection,
                "lane_projection": lane_projection,
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
        host_kind: str | None = None,
        host_mode: str | None = None,
    ) -> dict[str, Any]:
        exact_host = str(host_kind or "").strip().upper()
        exact_mode = str(host_mode or "").strip().upper()
        if exact_host.startswith("CHATGPT"):
            return {
                "status": "HOST_DEFERRED",
                "plan_persisted": False,
                "host_kind": exact_host,
                "host_mode": exact_mode or "NOT_DECLARED",
                "canonical_authority": "PLAN_LANE",
                "parked_scope": "CHATGPT_PLUGIN_LAYER",
                "reactivation_requires": "NEW_EXPLICIT_HUMAN_PLAN_AND_HIL",
                "message": (
                    "The ChatGPT plugin layer is parked and cannot add executable "
                    "rows to the Codex Goal projection."
                ),
            }
        if exact_host.startswith("CODEX") and exact_mode != "PLAN":
            return {
                "status": "PLAN_MODE_REQUIRED",
                "plan_persisted": False,
                "host_kind": exact_host,
                "host_mode": exact_mode or "NOT_DECLARED",
                "suggested_next_prompt": "/pl",
                "message": (
                    "Turn on Codex Plan mode with /pl, finish the plan, then run "
                    "/evi-plan again so Plan Lane and the native Goal/task panel pair."
                ),
                "chatgpt_plan_mode_assumed": False,
            }
        result = self.store.plan_tasks(
            project_id,
            tasks=tasks,
            planned_by=planned_by,
            plan_id=plan_id or prefixed_id("plan"),
        )
        result["host_plan_bridge"] = {
            "host_kind": exact_host or "UNDECLARED",
            "host_mode": exact_mode or "UNDECLARED",
            "canonical_authority": "PLAN_LANE",
            "codex_goal_start_prompt": result["goal_projection"][
                "goal_start_prompt"
            ],
            "copy_paste_required": exact_host.startswith("CODEX"),
            "host_goal_mutation_supported_by_mcp": False,
            "host_scope": "CODEX_ONLY",
        }
        return result

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
        return self.store.record_steer_delta(
            project_id,
            delta_text=delta_text,
            actor=actor,
            delta_id=delta_id,
            linked_task_id=linked_task_id,
            new_task_contract=new_task_contract,
            boundary=boundary,
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
            dirty_local_authority_context=(
                {
                    "session_id": session.session_id,
                    "task_id": str(task_payload["task_id"]),
                    "task_class": str(task_payload["task_class"]),
                    "lifecycle_state": session.state.value,
                    "task_contract_sha256": sha256_bytes(
                        canonical_json_bytes(task_payload)
                    ),
                }
                if replace_registered_branch
                and session is not None
                and permitted_paths is not None
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
                    "operation": result.get("operation"),
                    "dirty_worktree_preserved": result.get(
                        "dirty_worktree_preserved", False
                    ),
                    "worktree_status_sha256": result.get(
                        "worktree_status_sha256"
                    ),
                    "fetch_performed": result.get("fetch_performed", True),
                    "source_write_performed": result.get(
                        "source_write_performed", False
                    ),
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
            runtime_context=runtime_context,
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
        route_payload = self._persistence_route_payload(project_id, route)
        result = self.sessions.boot(
            project_id=project_id,
            user_id=user_id,
            workspace_id=workspace_id,
            host=host_kind,
            agent_id=agent_id,
            sandbox_id=sandbox_id,
            persistence_mode=route.mode,
            persistence_route=route_payload,
            ephemeral=ephemeral,
            runtime_context=runtime_context,
            flash=flash,
            host_session_id=host_session_id,
            client_can_edit_source=client_can_edit_source,
            server_has_durable_filesystem=route.server_filesystem == "DURABLE",
        )
        result["persistence_route"] = {
            **route_payload,
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
            runtime_context=runtime_context,
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
        route_payload = self._persistence_route_payload(project_id, route)
        result = self.sessions.resume(
            project_id=project_id,
            host=host_kind,
            host_session_id=host_session_id,
            persistence_mode=route.mode,
            persistence_route=route_payload,
            ephemeral=ephemeral,
            client_can_edit_source=client_can_edit_source,
            server_has_durable_filesystem=route.server_filesystem == "DURABLE",
            runtime_context=runtime_context,
            flash=flash,
        )
        result["session_flash"] = flash
        result["persistence_route"] = {
            **route_payload,
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
        *,
        resume_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Seal accepted context or the exact verified unfinished boundary."""

        return self.sessions.prepare_state_travel(
            project_id,
            session_id,
            resume_contract=resume_contract,
        )

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
        destination_preflight = self.sessions.validate_state_travel_destination(
            project_id,
            session_id,
            handoff_id=handoff_id,
            host=host,
            host_session_id=host_session_id,
            runtime_context=runtime_context,
        )
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
            destination_runtime_context=runtime_context,
        )
        travel_mode = verified["state_travel"].get("travel_mode", "ACCEPTED_ENTRY")
        ordered_entry_verification = (
            [
                "/evi-boot",
                "ATOMIC_BOOT_AND_LOCKED_ENV_UOP_FLASH_VERIFIED",
                "VERIFY_POINTER_BASE_AND_ANY_PRESERVED_CANDIDATE",
                "VERIFY_PLAN_LANE_SOURCE_AND_EXECUTION_PROFILE",
                "RESUME_EXACT_UNFINISHED_STEP",
            ]
            if travel_mode == "UNFINISHED_VERIFIED_WORK"
            else [
                "/evi-boot",
                "ATOMIC_BOOT_AND_LOCKED_ENV_UOP_FLASH_VERIFIED",
                "VERIFY_ACCEPTED_POINTER_AND_SEALS",
                "WAITING_FOR_NEXT_USER_COMMAND",
            ]
        )
        return {
            **verified,
            "ordered_entry_verification": ordered_entry_verification,
            "destination_preflight": destination_preflight,
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
        if batch_task_evidence is not None or batch_completion_confirmation is not None:
            require(
                batch_task_evidence is not None
                and batch_completion_confirmation is not None,
                "BATCH_DELTA_REFRESH_ARGUMENTS_INVALID",
                "Batch completion requires both exact evidence and confirmation.",
                status="BLOCKED",
            )
            exact_batch_evidence = cast(list[dict[str, Any]], batch_task_evidence)
            exact_batch_confirmation = cast(str, batch_completion_confirmation)
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
