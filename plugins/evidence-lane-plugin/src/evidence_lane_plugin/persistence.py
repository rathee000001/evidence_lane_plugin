"""User-owned local/Google Drive persistence boundary for sealed PV artifacts."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, TypedDict

import httpx

from .constants import POINTER_SCHEMA
from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes
from .models import HostKind, normalize_host_kind
from .runtime_host_classifier import classify_runtime_host
from .sealing import deterministic_archive, seal_archive
from .store import ProjectStore
from .timeutil import utc_now


class PersistenceBackend(Protocol):
    runtime_state_capable: bool

    def put(
        self,
        *,
        project_id: str,
        category: str,
        name: str,
        content: bytes,
        mime_type: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]: ...


@dataclass(slots=True)
class PersistenceRoute:
    mode: str
    reason: str
    durable_required: bool
    server_filesystem: str
    host_connector_role: str
    host_profile: str
    primary_runtime_authority: str
    google_drive_policy: str
    mcp_read_policy: str = "MCP_READ_TOOLS_AGAINST_PRIMARY_RUNTIME"
    mcp_write_policy: str = "MCP_MUTATION_TOOLS_UNDER_ENV_UOP_ONE_WRITER"
    env_continuity_policy: str = (
        "HASHED_ENV_UOP_REFERENCE_IN_ENTRY_EXIT_SLIPS_REFLASH_ON_BOOT_RESUME"
    )
    interaction_profile: str = "HOST_SURFACE_UNSPECIFIED"
    vm_lifetime: str = "LOCAL_OR_PERSISTENT"
    tunnel_requirement: str = "HOST_CAPABILITY_UNSPECIFIED"
    tunnel_setup_frequency: str = "HOST_CAPABILITY_UNSPECIFIED"
    tunnel_key_retention: str = "HOST_CAPABILITY_UNSPECIFIED"
    tunnel_runtime_lifetime: str = "HOST_CAPABILITY_UNSPECIFIED"
    host_tool_transport: str = "HOST_CAPABILITY_UNSPECIFIED"
    native_mcp_available: bool = False
    tool_gap_route: bool = False
    account_tier: str = "ACCOUNT_TIER_UNSPECIFIED"
    account_tier_affects_routing: bool = False
    api_billing_affects_routing: bool = False
    routing_axes_independent: bool = True
    runtime_classifier: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class _PersistenceHostMatrix(TypedDict):
    interaction_profile: str
    vm_lifetime: str
    tunnel_requirement: str
    tunnel_setup_frequency: str
    tunnel_key_retention: str
    tunnel_runtime_lifetime: str
    host_tool_transport: str
    native_mcp_available: bool
    tool_gap_route: bool
    account_tier: str
    account_tier_affects_routing: bool
    api_billing_affects_routing: bool
    routing_axes_independent: bool
    runtime_classifier: dict[str, Any]


def route_persistence(
    host: HostKind | str,
    *,
    ephemeral: bool,
    server_has_durable_filesystem: bool | None = None,
    runtime_context: dict[str, Any] | None = None,
    host_session_id: str | None = None,
) -> PersistenceRoute:
    """Route by the MCP server's storage capability, not UI brand alone."""
    kind = normalize_host_kind(host)
    context = dict(runtime_context or {})
    runtime_classifier = classify_runtime_host(
        kind,
        ephemeral=ephemeral,
        server_has_durable_filesystem=server_has_durable_filesystem,
        runtime_context=context,
        host_session_id=host_session_id,
    )
    requested_interaction = str(context.get("interaction_profile") or "").strip()
    interaction_aliases = {
        "API": "HEADLESS_API",
        "API_HEADLESS": "HEADLESS_API",
        "HEADLESS_API": "HEADLESS_API",
        "CODEX_APP_API": "HEADLESS_API",
        "CLI_API": "DIRECT_CLI_API",
        "DIRECT_CLI_API": "DIRECT_CLI_API",
        "CODEX_APP": "CODEX_APP_INTERACTIVE",
        "CODEX_APP_INTERACTIVE": "CODEX_APP_INTERACTIVE",
        "INTERACTIVE_CODEX_APP": "CODEX_APP_INTERACTIVE",
        "CODEX_CLI_NATIVE": "CODEX_CLI_NATIVE",
        "PUBLIC_AI": "PUBLIC_AI",
    }
    if requested_interaction:
        interaction_profile = interaction_aliases.get(
            requested_interaction.replace("-", "_").replace(" ", "_").upper()
        )
        require(
            interaction_profile is not None,
            "INTERACTION_PROFILE_INVALID",
            "The interaction profile is not part of the host capability matrix.",
            status="BLOCKED",
            provided=requested_interaction,
            supported=sorted(set(interaction_aliases.values())),
        )
        assert interaction_profile is not None
    elif kind == HostKind.CODEX_DESKTOP:
        interaction_profile = "CODEX_APP_INTERACTIVE"
    elif kind == HostKind.CODEX_CLI:
        interaction_profile = "CODEX_CLI_NATIVE"
    elif kind == HostKind.PUBLIC_AI:
        interaction_profile = "PUBLIC_AI"
    else:
        interaction_profile = "CODEX_VM_UNSPECIFIED"

    requested_tier = str(context.get("account_tier") or "").strip().upper()
    account_tier = requested_tier or "ACCOUNT_TIER_UNSPECIFIED"
    require(
        account_tier
        in {
            "ACCOUNT_TIER_UNSPECIFIED",
            "PRO",
            "PLUS",
            "BUSINESS",
            "EDU",
            "ENTERPRISE",
            "API",
        },
        "ACCOUNT_TIER_INVALID",
        "The supplied account tier is not a supported capability label.",
        status="BLOCKED",
        provided=requested_tier,
    )

    api_layer = interaction_profile in {"HEADLESS_API", "DIRECT_CLI_API"}
    interactive_codex_surface = interaction_profile in {
        "CODEX_APP_INTERACTIVE",
        "CODEX_CLI_NATIVE",
    }
    native_capabilities = dict(runtime_classifier.get("native_capabilities") or {})
    native_mcp_available = native_capabilities.get("native_mcp") is True
    if api_layer:
        tunnel_requirement = "NOT_REQUIRED_FOR_API_LAYER"
        tunnel_setup_frequency = "NONE"
        tunnel_key_retention = "NOT_APPLICABLE"
        tunnel_runtime_lifetime = "NOT_APPLICABLE"
        host_tool_transport = "API_DIRECT"
        tool_gap_route = False
    elif interactive_codex_surface and native_mcp_available:
        tunnel_requirement = "NOT_REQUIRED_NATIVE_MCP_AVAILABLE"
        tunnel_setup_frequency = "NONE"
        tunnel_key_retention = "NOT_APPLICABLE"
        tunnel_runtime_lifetime = "NOT_APPLICABLE"
        host_tool_transport = "NATIVE_MCP_AVAILABLE"
        tool_gap_route = False
    elif interactive_codex_surface:
        tunnel_requirement = "REQUIRED_FOR_HOST_TOOL_GAP"
        tunnel_setup_frequency = (
            "ONCE_PER_EPHEMERAL_VM_INSTANCE"
            if ephemeral
            else "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE"
        )
        tunnel_key_retention = (
            "CURRENT_VM_LIFETIME_ONLY"
            if ephemeral
            else "CURRENT_WINDOWS_USER_DPAPI_PROFILE"
        )
        tunnel_runtime_lifetime = (
            "CURRENT_VM_LIFETIME_ONLY"
            if ephemeral
            else "WINDOWS_LOGON_MANAGED_PERSISTENT_HOST"
        )
        host_tool_transport = "HOST_TOOL_GAP"
        tool_gap_route = True
    else:
        tunnel_requirement = "NOT_PART_OF_THIS_SURFACE_ROUTE"
        tunnel_setup_frequency = "NONE"
        tunnel_key_retention = "NOT_APPLICABLE"
        tunnel_runtime_lifetime = "NOT_APPLICABLE"
        host_tool_transport = "NOT_APPLICABLE"
        tool_gap_route = False

    host_matrix: _PersistenceHostMatrix = {
        "interaction_profile": interaction_profile,
        "vm_lifetime": "EPHEMERAL_VM" if ephemeral else "LOCAL_OR_PERSISTENT",
        "tunnel_requirement": tunnel_requirement,
        "tunnel_setup_frequency": tunnel_setup_frequency,
        "tunnel_key_retention": tunnel_key_retention,
        "tunnel_runtime_lifetime": tunnel_runtime_lifetime,
        "host_tool_transport": host_tool_transport,
        "native_mcp_available": native_mcp_available,
        "tool_gap_route": tool_gap_route,
        "account_tier": account_tier,
        "account_tier_affects_routing": False,
        "api_billing_affects_routing": False,
        "routing_axes_independent": True,
        "runtime_classifier": runtime_classifier,
    }
    durable_filesystem = (
        not ephemeral
        and kind
        in {
            HostKind.CODEX_DESKTOP,
            HostKind.CODEX_CLI,
            HostKind.CODEX_VM,
        }
        if server_has_durable_filesystem is None
        else bool(server_has_durable_filesystem)
    )
    if durable_filesystem:
        if kind == HostKind.CODEX_VM and ephemeral:
            return PersistenceRoute(
                mode="local",
                reason=(
                    "the ephemeral Codex VM reports an explicitly durable mounted "
                    "filesystem, so that mount owns the live SQLite authority"
                ),
                durable_required=False,
                server_filesystem="DURABLE",
                host_connector_role="EPHEMERAL_CODEX_DURABLE_MOUNT",
                host_profile="CODEX_EPHEMERAL_VM_WITH_DURABLE_MOUNT",
                primary_runtime_authority="DURABLE_MOUNT_SQLITE",
                google_drive_policy=(
                    "CODEX_EPHEMERAL_SEALED_ENTRY_EXIT_CARRIER_ALLOWED_NOT_PRIMARY"
                ),
                **host_matrix,
            )
        return PersistenceRoute(
            mode="local",
            reason=(
                "the MCP server has a durable user-owned filesystem for the "
                "immutable local store"
            ),
            durable_required=False,
            server_filesystem="DURABLE",
            host_connector_role="LOCAL_DURABLE_PRIMARY",
            host_profile=(
                "CODEX_STABLE_VM_OR_HOST"
                if kind == HostKind.CODEX_VM
                else "CODEX_LOCAL_PC_OR_LAPTOP"
                if kind in {HostKind.CODEX_DESKTOP, HostKind.CODEX_CLI}
                else "DURABLE_MCP_SERVER"
            ),
            primary_runtime_authority="LOCAL_DURABLE_SQLITE",
            google_drive_policy="NOT_SELECTED_FOR_DURABLE_HOST",
            **host_matrix,
        )

    if kind == HostKind.CODEX_VM and ephemeral:
        return PersistenceRoute(
            mode="configured_durable_connector",
            reason=(
                "the Codex VM is ephemeral and exposes no durable mount; live state "
                "requires a transactional runtime while Google Drive may carry only "
                "sealed Entry/Exit artifacts"
            ),
            durable_required=True,
            server_filesystem="EPHEMERAL_OR_UNAVAILABLE",
            host_connector_role="EPHEMERAL_CODEX_TRANSACTIONAL_RUNTIME_REQUIRED",
            host_profile="CODEX_EPHEMERAL_VM_WITHOUT_DURABLE_MOUNT",
            primary_runtime_authority="CONFIGURED_TRANSACTIONAL_RUNTIME_REQUIRED",
            google_drive_policy=(
                "CODEX_EPHEMERAL_SEALED_ENTRY_EXIT_CARRIER_ALLOWED_NOT_PRIMARY"
            ),
            **host_matrix,
        )
    return PersistenceRoute(
        mode="configured_durable_connector",
        reason=(
            "the MCP server has no durable filesystem, so the complete runtime "
            "state requires a configured transactional durable connector"
        ),
        durable_required=True,
        server_filesystem="EPHEMERAL_OR_UNAVAILABLE",
        host_connector_role="TRANSACTIONAL_RUNTIME_REQUIRED",
        host_profile=(
            "CODEX_HOST_WITHOUT_DURABLE_FILESYSTEM"
            if kind in {HostKind.CODEX_DESKTOP, HostKind.CODEX_CLI, HostKind.CODEX_VM}
            else "PUBLIC_AI_WITHOUT_DURABLE_MCP_HOST"
        ),
        primary_runtime_authority="CONFIGURED_TRANSACTIONAL_RUNTIME_REQUIRED",
        google_drive_policy="OPTIONAL_SEALED_MIRROR_NEVER_PRIMARY",
        **host_matrix,
    )


class InMemoryPersistence:
    """Test backend with the same object-level contract as Google Drive."""

    def __init__(self) -> None:
        self.runtime_state_capable = True
        self.objects: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.claims: dict[tuple[str, str, str], dict[str, Any]] = {}

    def put(
        self,
        *,
        project_id: str,
        category: str,
        name: str,
        content: bytes,
        mime_type: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        key = (project_id, category, name)
        digest = sha256_bytes(content)
        existing = self.objects.get(key)
        if existing:
            require(
                existing["sha256"] == digest,
                "PERSISTENCE_IMMUTABILITY_CONFLICT",
                "A durable object already exists with different bytes.",
                status="BLOCKED",
                object="/".join(key),
            )
            return existing
        receipt = {
            "backend": "memory-test",
            "project_id": project_id,
            "category": category,
            "name": name,
            "mime_type": mime_type,
            "bytes": len(content),
            "sha256": digest,
            "metadata": metadata,
            "stored_at": utc_now(),
        }
        self.objects[key] = receipt
        return receipt

    def claim_once(
        self,
        *,
        project_id: str,
        namespace: str,
        key: str,
        value_sha256: str,
        claimant_sha256: str,
    ) -> dict[str, Any]:
        """Atomically bind one test-runtime key to one exact claimant."""

        if namespace == "host-entry-consumption":
            persisted = self.objects.get(
                (project_id, "receipts", f"{key}.json")
            )
            require(
                persisted is not None
                and persisted["sha256"] == value_sha256,
                "PERSISTENCE_EXACT_ONCE_SOURCE_NOT_DURABLE",
                "A transactional claim requires the exact durably persisted source object.",
                status="BLOCKED",
                object=f"{project_id}/receipts/{key}.json",
            )
        claim_key = (project_id, namespace, key)
        existing = self.claims.get(claim_key)
        if existing is not None:
            require(
                existing["value_sha256"] == value_sha256
                and existing["claimant_sha256"] == claimant_sha256,
                "PERSISTENCE_EXACT_ONCE_CLAIM_CONFLICT",
                "A transactional runtime key was already claimed by different bytes.",
                status="BLOCKED",
                object="/".join(claim_key),
            )
            return {**existing, "idempotent": True}
        receipt = {
            "backend": "memory-test",
            "project_id": project_id,
            "namespace": namespace,
            "key": key,
            "value_sha256": value_sha256,
            "claimant_sha256": claimant_sha256,
            "claimed": True,
            "idempotent": False,
        }
        self.claims[claim_key] = receipt
        return receipt


class GoogleDrivePersistence:
    """Minimal REST adapter using only a user-supplied OAuth access token.

    The token is retained in memory only. The adapter writes within one explicit
    user-owned parent folder and never uploads a repository clone or cache.
    """

    API = "https://www.googleapis.com/drive/v3"
    UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
    runtime_state_capable = False

    def __init__(
        self,
        *,
        access_token: str,
        parent_folder_id: str,
        client: httpx.Client | None = None,
    ) -> None:
        require(
            bool(access_token.strip()),
            "GOOGLE_DRIVE_TOKEN_REQUIRED",
            "Google Drive persistence requires a user OAuth access token.",
            status="BLOCKED",
        )
        require(
            bool(parent_folder_id.strip()),
            "GOOGLE_DRIVE_FOLDER_REQUIRED",
            "Google Drive persistence requires one selected user-owned parent folder.",
            status="BLOCKED",
        )
        self._token = access_token
        self.parent_folder_id = parent_folder_id
        self._client = client or httpx.Client(timeout=60)

    @classmethod
    def from_environment(cls) -> GoogleDrivePersistence:
        return cls(
            access_token=os.environ.get("EVIDENCE_LANE_GOOGLE_DRIVE_ACCESS_TOKEN", ""),
            parent_folder_id=os.environ.get("EVIDENCE_LANE_GOOGLE_DRIVE_FOLDER_ID", ""),
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        response = self._client.request(method, url, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise EvidenceLaneError(
                "GOOGLE_DRIVE_REQUEST_FAILED",
                "Google Drive rejected a bounded persistence operation.",
                details={
                    "status_code": response.status_code,
                    "body": response.text[:2000],
                },
            )
        return response

    @staticmethod
    def _escape_query(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def _find_child(
        self, parent_id: str, name: str, mime_type: str | None
    ) -> dict[str, Any] | None:
        clauses = [
            f"'{self._escape_query(parent_id)}' in parents",
            f"name = '{self._escape_query(name)}'",
            "trashed = false",
        ]
        if mime_type:
            clauses.append(f"mimeType = '{self._escape_query(mime_type)}'")
        response = self._request(
            "GET",
            f"{self.API}/files",
            params={
                "q": " and ".join(clauses),
                "fields": "files(id,name,mimeType,md5Checksum,size,appProperties)",
                "pageSize": 10,
                "spaces": "drive",
            },
        )
        files = response.json().get("files", [])
        require(
            len(files) <= 1,
            "GOOGLE_DRIVE_DUPLICATE_OBJECT",
            "More than one Drive object matches the governed path.",
            status="MISMATCH",
            parent_id=parent_id,
            name=name,
        )
        return files[0] if files else None

    def _folder(self, parent_id: str, name: str) -> str:
        mime = "application/vnd.google-apps.folder"
        existing = self._find_child(parent_id, name, mime)
        if existing:
            return existing["id"]
        response = self._request(
            "POST",
            f"{self.API}/files",
            json={"name": name, "mimeType": mime, "parents": [parent_id]},
            params={"fields": "id,name,mimeType"},
        )
        return response.json()["id"]

    def _governed_parent(self, project_id: str, category: str) -> str:
        project = self._folder(self.parent_folder_id, project_id)
        return self._folder(project, category)

    def put(
        self,
        *,
        project_id: str,
        category: str,
        name: str,
        content: bytes,
        mime_type: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        require(
            category in {"accepted", "candidates", "receipts"},
            "DRIVE_CATEGORY_INVALID",
            "Only sealed PVs and bounded receipts may enter Drive persistence.",
            status="BLOCKED",
        )
        parent = self._governed_parent(project_id, category)
        digest = sha256_bytes(content)
        existing = self._find_child(parent, name, None)
        if existing:
            app_properties = existing.get("appProperties") or {}
            require(
                app_properties.get("sha256") == digest,
                "DRIVE_IMMUTABILITY_CONFLICT",
                "A Drive object already exists at this governed path with different bytes.",
                status="BLOCKED",
                name=name,
            )
            return {
                "backend": "google_drive",
                "file_id": existing["id"],
                "name": name,
                "sha256": digest,
                "idempotent": True,
            }
        boundary = "evidence_lane_boundary"
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        ).encode()
        body += canonical_json_bytes(
            {
                "name": name,
                "parents": [parent],
                "appProperties": {
                    "sha256": digest,
                    "project_id": project_id,
                    "category": category,
                },
            }
        )
        body += (f"\r\n--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n").encode()
        body += content + f"\r\n--{boundary}--\r\n".encode()
        response = self._request(
            "POST",
            f"{self.UPLOAD_API}/files",
            params={"uploadType": "multipart", "fields": "id,name,size,appProperties"},
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            content=body,
        )
        value = response.json()
        return {
            "backend": "google_drive",
            "file_id": value["id"],
            "name": name,
            "bytes": len(content),
            "sha256": digest,
            "metadata": metadata,
            "idempotent": False,
        }


class PVSyncService:
    def __init__(
        self,
        *,
        store: ProjectStore,
        backend: PersistenceBackend,
        drive_encryption_key: str | None = None,
    ) -> None:
        self.store = store
        self.backend = backend
        self.drive_encryption_key = drive_encryption_key

    @property
    def runtime_state_capable(self) -> bool:
        return bool(getattr(self.backend, "runtime_state_capable", False))

    def sync_pv(
        self,
        project_id: str,
        pv_ref: str,
        *,
        category: str,
    ) -> dict[str, Any]:
        if category == "accepted":
            package = self.store.accepted_path(project_id, pv_ref)
        elif category == "candidates":
            package = self.store.candidate_path(project_id, pv_ref)
        else:
            raise EvidenceLaneError(
                "PV_SYNC_CATEGORY_INVALID",
                "PV sync category must be accepted or candidates.",
                status="BLOCKED",
            )
        config = self.store.config(project_id)
        archive, metadata = deterministic_archive(package)
        sealed, sealed_metadata, extension = seal_archive(
            archive,
            metadata,
            key_value=self.drive_encryption_key,
            require_encryption=config.sensitivity.upper()
            in {"PRIVATE", "RESTRICTED", "CONFIDENTIAL"},
        )
        receipt = self.backend.put(
            project_id=project_id,
            category=category,
            name=f"{pv_ref}{extension}",
            content=sealed,
            mime_type=(
                "application/json" if extension.endswith(".json") else "application/zip"
            ),
            metadata=sealed_metadata,
        )
        return {
            "status": "PASS",
            "pv_ref": pv_ref,
            "category": category,
            "sealed": sealed_metadata,
            "persistence": receipt,
        }

    def sync_receipt(
        self,
        project_id: str,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        decision_id = str(
            receipt.get("decision_id")
            or receipt.get("action_id")
            or receipt.get("receipt_id")
            or ""
        )
        require(
            bool(decision_id),
            "RECEIPT_ID_REQUIRED",
            "A bounded receipt requires a stable ID before persistence.",
            status="BLOCKED",
        )
        payload = canonical_json_bytes(receipt)
        stored = self.backend.put(
            project_id=project_id,
            category="receipts",
            name=f"{decision_id}.json",
            content=payload,
            mime_type="application/json",
            metadata={
                "schema": receipt.get("schema"),
                "sha256": sha256_bytes(payload),
                "bytes": len(payload),
            },
        )
        return {
            "status": "PASS",
            "receipt_id": decision_id,
            "persistence": stored,
        }

    def sync_pointer(self, project_id: str) -> dict[str, Any]:
        """Persist one immutable snapshot of the current accepted pointer.

        The backend contract is append-only, so a pointer update is represented
        by a generation-addressed receipt instead of overwriting a mutable
        ``active_pointer.json`` object. A remote reader can therefore verify
        every state-travel event and select the highest generation without
        losing prior pointer history.
        """

        pointer = self.store.pointer(project_id)
        payload_value = {"schema": POINTER_SCHEMA, **pointer.as_dict()}
        payload = canonical_json_bytes(payload_value)
        accepted = pointer.accepted_pv or "NONE"
        name = f"active-pointer-gen-{pointer.generation:08d}-{accepted}.json"
        stored = self.backend.put(
            project_id=project_id,
            category="receipts",
            name=name,
            content=payload,
            mime_type="application/json",
            metadata={
                "schema": POINTER_SCHEMA,
                "generation": pointer.generation,
                "accepted_pv": pointer.accepted_pv,
                "sha256": sha256_bytes(payload),
                "bytes": len(payload),
            },
        )
        return {
            "status": "PASS",
            "pointer": pointer.as_dict(),
            "pointer_sha256": sha256_bytes(payload),
            "persistence": stored,
        }
