"""User-owned local/Google Drive persistence boundary for sealed PV artifacts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .constants import POINTER_SCHEMA
from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes
from .models import HostKind, normalize_host_kind
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


def route_persistence(
    host: HostKind | str,
    *,
    ephemeral: bool,
    server_has_durable_filesystem: bool | None = None,
) -> PersistenceRoute:
    """Route by the MCP server's storage capability, not UI brand alone."""
    kind = normalize_host_kind(host)
    durable_filesystem = (
        not ephemeral
        and kind
        in {
            HostKind.CODEX_DESKTOP,
            HostKind.CODEX_CLI,
        }
        if server_has_durable_filesystem is None
        else bool(server_has_durable_filesystem)
    )
    if ephemeral or not durable_filesystem:
        return PersistenceRoute(
            mode="configured_durable_connector",
            reason=(
                "the MCP server has no durable filesystem, so the complete runtime "
                "state requires a configured transactional durable connector"
            ),
            durable_required=True,
            server_filesystem="EPHEMERAL_OR_UNAVAILABLE",
            host_connector_role="OPTIONAL_FALLBACK_MIRROR_NEVER_PRIMARY",
        )
    return PersistenceRoute(
        mode="local",
        reason=(
            "the MCP server has a durable user-owned filesystem for the immutable "
            "local store"
        ),
        durable_required=False,
        server_filesystem="DURABLE",
        host_connector_role="OPTIONAL_VERIFIED_MIRROR",
    )


class InMemoryPersistence:
    """Test backend with the same object-level contract as Google Drive."""

    def __init__(self) -> None:
        self.runtime_state_capable = True
        self.objects: dict[tuple[str, str, str], dict[str, Any]] = {}

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
        root = self._folder(self.parent_folder_id, "EvidenceLanePV")
        project = self._folder(root, project_id)
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
