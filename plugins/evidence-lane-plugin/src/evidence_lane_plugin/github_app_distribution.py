"""Governed GitHub App runtime and tester-distribution contracts.

The module exposes a production GitHub REST seam and an authenticated public
webhook route without owning private keys, installation credentials, or raw
webhook payloads.  Secrets and short-lived bearer values exist only in
caller-owned memory; receipts contain hashes and authority-denial facts, never
the values themselves.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast
from urllib.parse import quote

import httpx
import jwt

from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes

GITHUB_APP_MANIFEST_SCHEMA = "evidence-lane.github-app-manifest.v1"
GITHUB_APP_DISTRIBUTION_ABI = "evidence-lane.github-app-distribution.v1"
GITHUB_APP_WEBHOOK_ROUTE = "/api/evidence-lane/github-app/webhook"
GITHUB_REST_API_VERSION = "2026-03-10"
EVIDENCE_LANE_APP_BOT_NAME = "evidence-lane[bot]"
EVIDENCE_LANE_APP_BOT_EMAIL = "319574480+evidence-lane[bot]@users.noreply.github.com"
_PROVIDER_EXPIRY_CLOCK_SKEW_SECONDS = 300

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SHA256 = re.compile(r"[A-F0-9]{64}")
_SIGNATURE = re.compile(r"sha256=([a-fA-F0-9]{64})")
_GIT_OID = re.compile(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?")
_PERMISSION_LEVELS = {"read", "write"}
_ALLOWED_REPOSITORY_PERMISSIONS: dict[str, set[str]] = {
    "actions": {"read"},
    "checks": {"read", "write"},
    "contents": {"read", "write"},
    "issues": {"read"},
    "metadata": {"read"},
    "pull_requests": {"read"},
    "workflows": {"read", "write"},
}
_ALLOWED_EVENTS = {
    "check_run",
    "check_suite",
    "installation",
    "installation_repositories",
    "push",
    "workflow_run",
}
_FORBIDDEN_SECRET_KEYS = {
    "client_secret",
    "private_key",
    "token",
    "webhook_secret",
}


def _receipt(schema: str, **fields: Any) -> dict[str, Any]:
    body = {"schema": schema, **fields}
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value).strip()
    require(
        bool(_IDENTIFIER.fullmatch(normalized)),
        "GITHUB_APP_CONTRACT_INVALID",
        f"{field} must be an exact bounded identifier.",
        status="BLOCKED",
        field=field,
    )
    return normalized


def _repository(value: object) -> str:
    normalized = str(value).strip().lower()
    require(
        bool(_REPOSITORY.fullmatch(normalized)),
        "GITHUB_APP_REPOSITORY_INVALID",
        "Repository scope must be one exact owner/repository identity.",
        status="BLOCKED",
    )
    return normalized


def _timestamp(value: object, *, field: str) -> datetime:
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.min.replace(tzinfo=UTC)
    require(
        parsed.tzinfo is not None and parsed != datetime.min.replace(tzinfo=UTC),
        "GITHUB_APP_TIME_INVALID",
        f"{field} must be an ISO-8601 timestamp with timezone.",
        status="BLOCKED",
        field=field,
    )
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _sha256(value: object, *, field: str) -> str:
    normalized = str(value).strip().upper()
    require(
        bool(_SHA256.fullmatch(normalized)),
        "GITHUB_APP_SHA256_INVALID",
        f"{field} must be one exact SHA-256 identity.",
        status="BLOCKED",
        field=field,
    )
    return normalized


def _git_oid(value: object, *, field: str) -> str:
    normalized = str(value).strip().lower()
    require(
        bool(_GIT_OID.fullmatch(normalized)),
        "GITHUB_APP_GIT_IDENTITY_INVALID",
        f"{field} must be one exact Git object identity.",
        status="BLOCKED",
        field=field,
    )
    return normalized


def _bounded_text(value: object, *, field: str, limit: int = 256) -> str:
    normalized = str(value).strip()
    require(
        bool(normalized) and len(normalized.encode("utf-8")) <= limit,
        "GITHUB_APP_CONTRACT_INVALID",
        f"{field} must be non-empty and bounded.",
        status="BLOCKED",
        field=field,
    )
    return normalized


def _bounded_commit_message(value: object) -> str:
    require(
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= 64 * 1024
        and "\x00" not in value,
        "GITHUB_APP_COMMIT_MESSAGE_INVALID",
        "The exact Git commit message must be non-empty UTF-8 and bounded.",
        status="BLOCKED",
    )
    return cast(str, value)


def _permission_pairs(value: object) -> tuple[tuple[str, str], ...]:
    require(
        isinstance(value, Mapping),
        "GITHUB_APP_PERMISSIONS_INVALID",
        "Repository permissions must be an exact object.",
        status="BLOCKED",
    )
    pairs: list[tuple[str, str]] = []
    for raw_name, raw_level in cast(Mapping[object, object], value).items():
        name = str(raw_name).strip().lower()
        level = str(raw_level).strip().lower()
        allowed = _ALLOWED_REPOSITORY_PERMISSIONS.get(name, set())
        require(
            level in _PERMISSION_LEVELS and level in allowed,
            "GITHUB_APP_PERMISSION_OVERBROAD",
            "The requested GitHub App permission is absent or broader than the pre-HIL contract.",
            status="BLOCKED",
            permission=name,
            requested_level=level,
            allowed_levels=sorted(allowed),
        )
        pairs.append((name, level))
    normalized = tuple(sorted(set(pairs)))
    require(
        ("metadata", "read") in normalized,
        "GITHUB_APP_METADATA_PERMISSION_REQUIRED",
        "The manifest must explicitly retain GitHub metadata read authority.",
        status="BLOCKED",
    )
    return normalized


def _permission_map(value: Sequence[tuple[str, str]]) -> dict[str, str]:
    return {name: level for name, level in value}


@dataclass(frozen=True, slots=True)
class GitHubAppManifest:
    """Non-secret manifest and least-privilege permission contract."""

    app_slug: str
    manifest_version: str
    repository_selection: str
    repository_permissions: tuple[tuple[str, str], ...]
    events: tuple[str, ...]
    public: bool = False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> GitHubAppManifest:
        expected = {
            "schema",
            "app_slug",
            "manifest_version",
            "repository_selection",
            "repository_permissions",
            "events",
            "public",
        }
        require(
            set(raw) == expected,
            "GITHUB_APP_MANIFEST_FIELDS_INVALID",
            "The manifest must contain exactly the non-secret contract fields.",
            status="BLOCKED",
            missing=sorted(expected - set(raw)),
            unknown=sorted(set(raw) - expected),
        )
        require(
            raw["schema"] == GITHUB_APP_MANIFEST_SCHEMA,
            "GITHUB_APP_MANIFEST_SCHEMA_INVALID",
            "The GitHub App manifest schema identity is unsupported.",
            status="BLOCKED",
        )
        selection = str(raw["repository_selection"]).strip().lower()
        require(
            selection == "selected",
            "GITHUB_APP_REPOSITORY_SELECTION_OVERBROAD",
            "Pre-HIL distribution defaults to explicitly selected repositories.",
            status="BLOCKED",
        )
        events_raw = raw["events"]
        require(
            isinstance(events_raw, (list, tuple)),
            "GITHUB_APP_EVENTS_INVALID",
            "Webhook events must be an ordered list.",
            status="BLOCKED",
        )
        events = tuple(
            sorted({str(item).strip() for item in cast(Sequence[object], events_raw)})
        )
        require(
            bool(events) and set(events) <= _ALLOWED_EVENTS,
            "GITHUB_APP_EVENTS_OVERBROAD",
            "The manifest requests an unsupported webhook event.",
            status="BLOCKED",
            unsupported=sorted(set(events) - _ALLOWED_EVENTS),
        )
        require(
            raw["public"] is False,
            "GITHUB_APP_PUBLICATION_PRE_HIL_BLOCKED",
            "Public listing remains a separately authorized post-HIL action.",
            status="BLOCKED",
        )
        return cls(
            app_slug=_identifier(raw["app_slug"], field="app_slug").lower(),
            manifest_version=_identifier(
                raw["manifest_version"], field="manifest_version"
            ),
            repository_selection=selection,
            repository_permissions=_permission_pairs(raw["repository_permissions"]),
            events=events,
            public=False,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": GITHUB_APP_MANIFEST_SCHEMA,
            "app_slug": self.app_slug,
            "manifest_version": self.manifest_version,
            "repository_selection": self.repository_selection,
            "repository_permissions": _permission_map(self.repository_permissions),
            "events": list(self.events),
            "public": self.public,
        }

    @property
    def sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.as_dict()))


@dataclass(frozen=True, slots=True)
class InstallationBinding:
    """One project/task-bound, repository-specific installation capability."""

    binding_id: str
    app_slug: str
    installation_id: str
    project_id: str
    task_id: str
    accepted_pv: str
    repositories: tuple[str, ...]
    permissions: tuple[tuple[str, str], ...]
    expires_at: str

    @classmethod
    def create(
        cls,
        *,
        binding_id: str,
        manifest: GitHubAppManifest,
        installation_id: str,
        project_id: str,
        task_id: str,
        accepted_pv: str,
        repositories: Sequence[str],
        permissions: Mapping[str, str],
        expires_at: str,
    ) -> InstallationBinding:
        exact_repositories = tuple(sorted({_repository(item) for item in repositories}))
        require(
            bool(exact_repositories),
            "GITHUB_APP_REPOSITORY_SCOPE_REQUIRED",
            "An installation binding requires at least one exact repository.",
            status="BLOCKED",
        )
        exact_permissions = _permission_pairs(permissions)
        manifest_permissions = dict(manifest.repository_permissions)
        require(
            all(
                manifest_permissions.get(name) == level
                for name, level in exact_permissions
            ),
            "GITHUB_APP_INSTALLATION_PERMISSION_MISMATCH",
            "Installation permissions must be a subset of the sealed manifest.",
            status="BLOCKED",
        )
        _timestamp(expires_at, field="expires_at")
        return cls(
            binding_id=_identifier(binding_id, field="binding_id"),
            app_slug=manifest.app_slug,
            installation_id=_identifier(installation_id, field="installation_id"),
            project_id=_identifier(project_id, field="project_id"),
            task_id=_identifier(task_id, field="task_id"),
            accepted_pv=_identifier(accepted_pv, field="accepted_pv"),
            repositories=exact_repositories,
            permissions=exact_permissions,
            expires_at=expires_at,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "evidence-lane.github-app-installation-binding.v1",
            "binding_id": self.binding_id,
            "app_slug": self.app_slug,
            "installation_id": self.installation_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "accepted_pv": self.accepted_pv,
            "repositories": list(self.repositories),
            "permissions": _permission_map(self.permissions),
            "expires_at": self.expires_at,
        }

    @property
    def sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.as_dict()))


@dataclass(frozen=True, slots=True)
class InstallationTokenRequest:
    request_id: str
    idempotency_key: str
    project_id: str
    task_id: str
    installation_id: str
    repository: str
    permissions: tuple[tuple[str, str], ...]
    requested_at: str
    expires_at: str

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        idempotency_key: str,
        project_id: str,
        task_id: str,
        installation_id: str,
        repository: str,
        permissions: Mapping[str, str],
        requested_at: str,
        expires_at: str,
    ) -> InstallationTokenRequest:
        _timestamp(requested_at, field="requested_at")
        _timestamp(expires_at, field="expires_at")
        return cls(
            request_id=_identifier(request_id, field="request_id"),
            idempotency_key=_identifier(idempotency_key, field="idempotency_key"),
            project_id=_identifier(project_id, field="project_id"),
            task_id=_identifier(task_id, field="task_id"),
            installation_id=_identifier(installation_id, field="installation_id"),
            repository=_repository(repository),
            permissions=_permission_pairs(permissions),
            requested_at=requested_at,
            expires_at=expires_at,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "installation_id": self.installation_id,
            "repository": self.repository,
            "permissions": _permission_map(self.permissions),
            "requested_at": self.requested_at,
            "expires_at": self.expires_at,
        }


class InstallationTokenProvider(Protocol):
    """Provider-neutral token seam; returned values are never receipt fields."""

    provider_id: str

    def issue(self, request: InstallationTokenRequest) -> str: ...


class GitHubAppJWTProvider(Protocol):
    """Caller-owned signer seam; private-key bytes never enter this module."""

    provider_id: str

    def issue_app_jwt(self, *, requested_at: str) -> str: ...


@dataclass(frozen=True, slots=True)
class GitHubAPIResponse:
    """Bounded response returned by an injected GitHub HTTPS transport."""

    status_code: int
    body: Mapping[str, Any]
    request_id: str | None = None


class GitHubJSONTransport(Protocol):
    """Exact GitHub JSON transport seam used by the production provider."""

    transport_id: str

    def request_json(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
    ) -> GitHubAPIResponse: ...


class InMemoryPEMGitHubAppJWTProvider:
    """Create bounded RS256 App JWTs while keeping PEM bytes in memory only."""

    provider_id = "GITHUB_APP_IN_MEMORY_PEM_RS256_V1"

    def __init__(self, *, client_id: str, private_key_pem: bytes) -> None:
        self.client_id = _bounded_text(client_id, field="client_id", limit=128)
        require(
            b"-----BEGIN" in private_key_pem
            and b"PRIVATE KEY-----" in private_key_pem
            and len(private_key_pem) <= 64 * 1024,
            "GITHUB_APP_PRIVATE_KEY_INVALID",
            "The in-memory GitHub App signer requires one bounded PEM private key.",
            status="BLOCKED",
        )
        self._private_key_pem = bytes(private_key_pem)

    def issue_app_jwt(self, *, requested_at: str) -> str:
        requested = _timestamp(requested_at, field="requested_at")
        payload = {
            "iat": int((requested - timedelta(seconds=60)).timestamp()),
            "exp": int((requested + timedelta(minutes=9)).timestamp()),
            "iss": self.client_id,
        }
        encoded = jwt.encode(
            payload,
            self._private_key_pem,
            algorithm="RS256",
        )
        require(
            isinstance(encoded, str) and 16 <= len(encoded) <= 8192,
            "GITHUB_APP_JWT_INVALID",
            "The GitHub App signer did not produce one bounded JWT.",
            status="BLOCKED",
        )
        return encoded


class HttpxGitHubJSONTransport:
    """HTTPS-only GitHub JSON transport with bounded mapping responses."""

    transport_id = "GITHUB_HTTPX_JSON_TRANSPORT_V1"

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        base_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
    ) -> None:
        normalized_base = base_url.rstrip("/")
        require(
            normalized_base == "https://api.github.com",
            "GITHUB_APP_TRANSPORT_ORIGIN_INVALID",
            "The production GitHub transport is pinned to the official HTTPS API origin.",
            status="BLOCKED",
        )
        require(
            1.0 <= float(timeout_seconds) <= 60.0,
            "GITHUB_APP_TRANSPORT_TIMEOUT_INVALID",
            "The GitHub HTTPS timeout must remain bounded.",
            status="BLOCKED",
        )
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=normalized_base,
            timeout=float(timeout_seconds),
            follow_redirects=False,
        )

    def request_json(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
    ) -> GitHubAPIResponse:
        exact_method = str(method).strip().upper()
        require(
            exact_method in {"GET", "POST", "PATCH"}
            and path.startswith("/")
            and not path.startswith("//")
            and "\r" not in path
            and "\n" not in path,
            "GITHUB_APP_TRANSPORT_REQUEST_INVALID",
            "The GitHub HTTPS request method or relative path is invalid.",
            status="BLOCKED",
        )
        response = self._client.request(
            exact_method,
            path,
            headers=dict(headers),
            json=dict(body) if body else None,
        )
        try:
            decoded = response.json() if response.content else {}
        except ValueError as exc:
            require(
                False,
                "GITHUB_APP_TRANSPORT_RESPONSE_INVALID",
                "GitHub returned a non-JSON response to the JSON API route.",
                status="BLOCKED",
                http_status=response.status_code,
                error=type(exc).__name__,
            )
            raise AssertionError("unreachable") from exc
        require(
            isinstance(decoded, Mapping),
            "GITHUB_APP_TRANSPORT_RESPONSE_INVALID",
            "GitHub returned a non-object JSON response to the bounded route.",
            status="BLOCKED",
            http_status=response.status_code,
        )
        request_id = response.headers.get("x-github-request-id")
        return GitHubAPIResponse(
            status_code=response.status_code,
            body=cast(Mapping[str, Any], decoded),
            request_id=request_id,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class DeterministicMockGitHubProvider:
    """Disposable pre-HIL provider with no network or production credentials."""

    provider_id = "GITHUB_DETERMINISTIC_MOCK"

    def __init__(self, seed: bytes) -> None:
        require(
            len(seed) >= 16,
            "GITHUB_APP_MOCK_SEED_INVALID",
            "The deterministic mock requires at least 16 bytes of test seed.",
            status="BLOCKED",
        )
        self._seed = bytes(seed)
        self.calls: list[str] = []

    def issue(self, request: InstallationTokenRequest) -> str:
        request_sha = sha256_bytes(canonical_json_bytes(request.as_dict()))
        self.calls.append(request_sha)
        digest = hmac.new(
            self._seed, request_sha.encode("ascii"), hashlib.sha256
        ).hexdigest()
        return f"mock-installation-token-{digest}"


class GitHubRESTInstallationTokenProvider:
    """Production GitHub REST adapter with externally owned JWT and transport.

    The adapter implements GitHub's installation-token endpoint rather than a
    mock surface.  Signing keys, app JWTs, and returned bearer tokens remain in
    caller-owned memory.  Only a redacted integration-proof receipt is retained.
    """

    provider_id = "GITHUB_REST_INSTALLATION_TOKEN_V1"

    def __init__(
        self,
        *,
        jwt_provider: GitHubAppJWTProvider,
        transport: GitHubJSONTransport,
        api_version: str = GITHUB_REST_API_VERSION,
    ) -> None:
        self.jwt_provider = jwt_provider
        self.transport = transport
        self.api_version = _identifier(api_version, field="api_version")
        self.last_integration_receipt: dict[str, Any] | None = None

    def issue(self, request: InstallationTokenRequest) -> str:
        require(
            request.installation_id.isdecimal(),
            "GITHUB_APP_INSTALLATION_ID_INVALID",
            "The production GitHub route requires one numeric installation ID.",
            status="BLOCKED",
        )
        app_jwt = self.jwt_provider.issue_app_jwt(requested_at=request.requested_at)
        require(
            16 <= len(app_jwt) <= 8192
            and not any(char in app_jwt for char in "\r\n\x00"),
            "GITHUB_APP_JWT_INVALID",
            "The external JWT provider returned an invalid in-memory credential.",
            status="BLOCKED",
        )
        path = f"/app/installations/{request.installation_id}/access_tokens"
        request_body = {
            "repositories": [request.repository.split("/", 1)[1]],
            "permissions": _permission_map(request.permissions),
        }
        response = self.transport.request_json(
            method="POST",
            path=path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {app_jwt}",
                "X-GitHub-Api-Version": self.api_version,
            },
            body=request_body,
        )
        require(
            response.status_code == 201,
            "GITHUB_APP_PROVIDER_REQUEST_FAILED",
            "GitHub did not create an installation access token.",
            status="BLOCKED",
            http_status=response.status_code,
        )
        payload = response.body
        token = str(payload.get("token") or "")
        require(
            16 <= len(token) <= 8192 and not any(char in token for char in "\r\n\x00"),
            "GITHUB_APP_PROVIDER_TOKEN_INVALID",
            "GitHub returned an invalid in-memory installation token.",
            status="BLOCKED",
        )
        expires_at = _timestamp(payload.get("expires_at"), field="expires_at")
        requested_at = _timestamp(request.requested_at, field="requested_at")
        require(
            0
            < (expires_at - requested_at).total_seconds()
            <= 3600 + _PROVIDER_EXPIRY_CLOCK_SKEW_SECONDS,
            "GITHUB_APP_PROVIDER_EXPIRY_INVALID",
            "GitHub returned an installation token outside the one-hour bound and "
            "the explicit five-minute provider clock-skew allowance.",
            status="BLOCKED",
        )
        permissions = _permission_pairs(payload.get("permissions"))
        require(
            permissions == request.permissions,
            "GITHUB_APP_PROVIDER_PERMISSION_DRIFT",
            "GitHub returned permissions that differ from the exact request.",
            status="BLOCKED",
        )
        require(
            str(payload.get("repository_selection") or "").strip().lower()
            == "selected",
            "GITHUB_APP_PROVIDER_REPOSITORY_SELECTION_DRIFT",
            "GitHub returned an installation token outside selected-repository scope.",
            status="BLOCKED",
        )
        repositories_raw = payload.get("repositories")
        require(
            isinstance(repositories_raw, (list, tuple)),
            "GITHUB_APP_PROVIDER_REPOSITORIES_INVALID",
            "GitHub did not return the selected repository proof.",
            status="BLOCKED",
        )
        repositories = tuple(
            sorted(
                _repository(item.get("full_name"))
                for item in cast(Sequence[object], repositories_raw)
                if isinstance(item, Mapping)
            )
        )
        require(
            repositories == (request.repository,),
            "GITHUB_APP_PROVIDER_REPOSITORY_DRIFT",
            "GitHub returned repository scope that differs from the exact request.",
            status="BLOCKED",
        )
        safe_payload = {
            key: ("<REDACTED>" if key == "token" else value)
            for key, value in payload.items()
        }
        request_id = (
            _identifier(response.request_id, field="request_id")
            if response.request_id is not None
            else None
        )
        self.last_integration_receipt = _receipt(
            "evidence-lane.github-app-provider-integration-receipt.v1",
            status="PASS",
            provider_id=self.provider_id,
            jwt_provider_id=_identifier(
                self.jwt_provider.provider_id, field="jwt_provider_id"
            ),
            transport_id=_identifier(self.transport.transport_id, field="transport_id"),
            api_version=self.api_version,
            method="POST",
            path=path,
            request_id=request_id,
            request_body_sha256=sha256_bytes(canonical_json_bytes(request_body)),
            response_status=response.status_code,
            safe_response_sha256=sha256_bytes(canonical_json_bytes(safe_payload)),
            repository=request.repository,
            permissions=_permission_map(permissions),
            expires_at=_iso(expires_at),
            app_jwt_persisted=False,
            installation_token_persisted=False,
            source_write_authorized=(dict(permissions).get("contents") == "write"),
            workflow_write_authorized=(dict(permissions).get("workflows") == "write"),
            pointer_moved=False,
            hil_inferred=False,
        )
        return token


class InstallationTokenBroker:
    """Validate exact scope before requesting one short-lived in-memory token."""

    def __init__(
        self,
        *,
        manifest: GitHubAppManifest,
        binding: InstallationBinding,
        provider: InstallationTokenProvider,
        max_ttl_seconds: int = 3600,
        max_request_age_seconds: int = 300,
    ) -> None:
        self.manifest = manifest
        self.binding = binding
        self.provider = provider
        self.max_ttl_seconds = max_ttl_seconds
        self.max_request_age_seconds = max_request_age_seconds
        self._replay: dict[str, tuple[str, str, dict[str, Any]]] = {}

    def issue(
        self, request: InstallationTokenRequest, *, now: str
    ) -> tuple[str, dict[str, Any]]:
        exact_now = _timestamp(now, field="now")
        requested = _timestamp(request.requested_at, field="requested_at")
        expires = _timestamp(request.expires_at, field="expires_at")
        binding_expires = _timestamp(
            self.binding.expires_at, field="binding.expires_at"
        )
        require(
            0 < (expires - exact_now).total_seconds() <= self.max_ttl_seconds,
            "GITHUB_APP_TOKEN_TTL_INVALID",
            "The installation-token request is stale or exceeds the bounded TTL.",
            status="BLOCKED",
        )
        require(
            abs((exact_now - requested).total_seconds())
            <= self.max_request_age_seconds,
            "GITHUB_APP_TOKEN_REQUEST_STALE",
            "The installation-token request timestamp is stale.",
            status="BLOCKED",
        )
        require(
            expires <= binding_expires,
            "GITHUB_APP_BINDING_EXPIRES_FIRST",
            "The requested token outlives its installation binding.",
            status="BLOCKED",
        )
        require(
            request.project_id == self.binding.project_id
            and request.task_id == self.binding.task_id
            and request.installation_id == self.binding.installation_id,
            "GITHUB_APP_TOKEN_BINDING_MISMATCH",
            "The token request does not match the project/task/installation binding.",
            status="BLOCKED",
        )
        require(
            request.repository in self.binding.repositories,
            "GITHUB_APP_CROSS_REPOSITORY_ACCESS_BLOCKED",
            "The requested repository is outside this installation binding.",
            status="BLOCKED",
        )
        binding_permissions = dict(self.binding.permissions)
        require(
            all(
                binding_permissions.get(name) == level
                for name, level in request.permissions
            ),
            "GITHUB_APP_TOKEN_PERMISSION_OVERBROAD",
            "The token request exceeds the exact installation permission set.",
            status="BLOCKED",
        )
        request_sha = sha256_bytes(canonical_json_bytes(request.as_dict()))
        prior = self._replay.get(request.idempotency_key)
        if prior is not None:
            require(
                prior[0] == request_sha,
                "GITHUB_APP_TOKEN_REPLAY_CONFLICT",
                "The idempotency key was reused with different request bytes.",
                status="BLOCKED",
            )
            return prior[1], {**prior[2], "idempotent_reuse": True}
        token = self.provider.issue(request)
        require(
            bool(token),
            "GITHUB_APP_TOKEN_PROVIDER_EMPTY",
            "The provider returned no in-memory bearer value.",
            status="BLOCKED",
        )
        receipt = _receipt(
            "evidence-lane.github-app-token-broker-receipt.v1",
            status="PASS",
            abi=GITHUB_APP_DISTRIBUTION_ABI,
            provider_id=self.provider.provider_id,
            request_sha256=request_sha,
            binding_sha256=self.binding.sha256,
            repository=request.repository,
            permissions=_permission_map(request.permissions),
            expires_at=_iso(expires),
            token_sha256=sha256_bytes(token.encode("utf-8")),
            token_value_persisted=False,
            idempotent_reuse=False,
            source_write_authorized=(
                dict(request.permissions).get("contents") == "write"
            ),
            workflow_write_authorized=(
                dict(request.permissions).get("workflows") == "write"
            ),
            pointer_moved=False,
            hil_inferred=False,
        )
        self._replay[request.idempotency_key] = (request_sha, token, receipt)
        return token, receipt


def _git_blob_sha1(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _git_branch(value: object) -> str:
    branch = _bounded_text(value, field="branch", limit=240)
    invalid = (
        branch.startswith(("/", "refs/"))
        or branch.endswith(("/", ".", ".lock"))
        or ".." in branch
        or "@{" in branch
        or any(character in branch for character in " ~^:?*[\\\r\n\x00")
        or any(part in {"", ".", ".."} for part in branch.split("/"))
    )
    require(
        not invalid,
        "GITHUB_APP_BRANCH_INVALID",
        "The exact GitHub branch is not a valid bounded branch identity.",
        status="BLOCKED",
    )
    return branch


def _git_path(value: object) -> str:
    path = str(value).replace("\\", "/").strip()
    parts = path.split("/")
    require(
        bool(path)
        and len(path.encode("utf-8")) <= 1024
        and not path.startswith("/")
        and all(part not in {"", ".", ".."} for part in parts)
        and not any(character in path for character in "\r\n\x00"),
        "GITHUB_APP_TREE_PATH_INVALID",
        "Each exact Git tree change requires one repository-relative path.",
        status="BLOCKED",
    )
    return path


@dataclass(frozen=True, slots=True)
class GitCommitActor:
    """Exact author/committer identity used to reproduce a local commit."""

    name: str
    email: str
    date: str

    @classmethod
    def create(cls, *, name: str, email: str, date: str) -> GitCommitActor:
        exact_email = _bounded_text(email, field="email", limit=320)
        require(
            "@" in exact_email and not any(char in exact_email for char in "\r\n\x00"),
            "GITHUB_APP_COMMIT_ACTOR_INVALID",
            "The exact Git commit actor email is invalid.",
            status="BLOCKED",
        )
        exact_date = str(date).strip()
        try:
            parsed_date: datetime | None = datetime.fromisoformat(exact_date)
        except ValueError:
            parsed_date = None
        require(
            parsed_date is not None and parsed_date.tzinfo is not None,
            "GITHUB_APP_COMMIT_ACTOR_INVALID",
            "The exact Git commit actor date must include its timezone.",
            status="BLOCKED",
        )
        assert parsed_date is not None
        normalized_date = parsed_date.isoformat(timespec="seconds")
        return cls(
            name=_bounded_text(name, field="name", limit=256),
            email=exact_email,
            date=normalized_date,
        )

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "email": self.email, "date": self.date}


@dataclass(frozen=True, slots=True)
class GitTreeChange:
    """One exact blob write or deletion in a Git Database API tree."""

    path: str
    mode: str
    content: bytes | None
    blob_sha: str | None

    @classmethod
    def create(
        cls,
        *,
        path: str,
        mode: str,
        content: bytes | None,
    ) -> GitTreeChange:
        exact_mode = str(mode).strip()
        require(
            exact_mode in {"100644", "100755", "120000"},
            "GITHUB_APP_TREE_MODE_INVALID",
            "The exact Git tree route supports regular, executable, and symlink blobs.",
            status="BLOCKED",
        )
        exact_content = bytes(content) if content is not None else None
        require(
            exact_content is None or len(exact_content) <= 100 * 1024 * 1024,
            "GITHUB_APP_BLOB_BOUND_EXCEEDED",
            "A GitHub Git Database blob exceeds the 100 MB API bound.",
            status="BLOCKED",
        )
        return cls(
            path=_git_path(path),
            mode=exact_mode,
            content=exact_content,
            blob_sha=(
                _git_blob_sha1(exact_content) if exact_content is not None else None
            ),
        )

    @property
    def operation(self) -> str:
        return "DELETE" if self.content is None else "UPSERT"

    def identity(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "mode": self.mode,
            "operation": self.operation,
            "blob_sha": self.blob_sha,
            "content_sha256": (
                sha256_bytes(self.content) if self.content is not None else None
            ),
            "content_bytes": len(self.content) if self.content is not None else 0,
        }


@dataclass(frozen=True, slots=True)
class ExactGitCommitPushRequest:
    """Exact local commit identity and bounded tree delta for one App push."""

    request_id: str
    idempotency_key: str
    project_id: str
    task_id: str
    repository: str
    branch: str
    expected_parent_commit_sha: str
    additional_parent_commit_shas: tuple[str, ...]
    expected_parent_tree_sha: str
    expected_tree_sha: str
    expected_commit_sha: str
    commit_message: str
    author: GitCommitActor
    committer: GitCommitActor
    changes: tuple[GitTreeChange, ...]

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        idempotency_key: str,
        project_id: str,
        task_id: str,
        repository: str,
        branch: str,
        expected_parent_commit_sha: str,
        additional_parent_commit_shas: Sequence[str] = (),
        expected_parent_tree_sha: str,
        expected_tree_sha: str,
        expected_commit_sha: str,
        commit_message: str,
        author: GitCommitActor,
        committer: GitCommitActor,
        changes: Sequence[GitTreeChange],
    ) -> ExactGitCommitPushRequest:
        exact_changes = tuple(sorted(changes, key=lambda item: item.path))
        paths = [item.path for item in exact_changes]
        require(
            bool(exact_changes)
            and len(exact_changes) <= 10_000
            and len(paths) == len(set(paths))
            and sum(len(item.content or b"") for item in exact_changes)
            <= 250 * 1024 * 1024,
            "GITHUB_APP_TREE_DELTA_INVALID",
            "The exact Git tree delta must be non-empty, unique, and bounded.",
            status="BLOCKED",
        )
        exact_parent = _git_oid(
            expected_parent_commit_sha,
            field="expected_parent_commit_sha",
        )
        additional_parents = tuple(
            _git_oid(value, field="additional_parent_commit_sha")
            for value in additional_parent_commit_shas
        )
        require(
            len(additional_parents) <= 7
            and len(additional_parents) == len(set(additional_parents))
            and exact_parent not in additional_parents,
            "GITHUB_APP_COMMIT_PARENT_SET_INVALID",
            "The exact App commit parent set must be bounded, unique, and ordered.",
            status="BLOCKED",
        )
        require(
            author.name == EVIDENCE_LANE_APP_BOT_NAME
            and author.email == EVIDENCE_LANE_APP_BOT_EMAIL
            and committer.name == EVIDENCE_LANE_APP_BOT_NAME
            and committer.email == EVIDENCE_LANE_APP_BOT_EMAIL,
            "GITHUB_APP_BOT_ACTOR_REQUIRED",
            "Evidence Lane App commits require the canonical bot as both author and committer.",
            status="BLOCKED",
        )
        return cls(
            request_id=_identifier(request_id, field="request_id"),
            idempotency_key=_identifier(idempotency_key, field="idempotency_key"),
            project_id=_identifier(project_id, field="project_id"),
            task_id=_identifier(task_id, field="task_id"),
            repository=_repository(repository),
            branch=_git_branch(branch),
            expected_parent_commit_sha=exact_parent,
            additional_parent_commit_shas=additional_parents,
            expected_parent_tree_sha=_git_oid(
                expected_parent_tree_sha,
                field="expected_parent_tree_sha",
            ),
            expected_tree_sha=_git_oid(expected_tree_sha, field="expected_tree_sha"),
            expected_commit_sha=_git_oid(
                expected_commit_sha,
                field="expected_commit_sha",
            ),
            commit_message=_bounded_commit_message(commit_message),
            author=author,
            committer=committer,
            changes=exact_changes,
        )

    def identity(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "repository": self.repository,
            "branch": self.branch,
            "expected_parent_commit_sha": self.expected_parent_commit_sha,
            "additional_parent_commit_shas": list(self.additional_parent_commit_shas),
            "expected_parent_tree_sha": self.expected_parent_tree_sha,
            "expected_tree_sha": self.expected_tree_sha,
            "expected_commit_sha": self.expected_commit_sha,
            "commit_message_sha256": sha256_bytes(self.commit_message.encode("utf-8")),
            "author": self.author.as_dict(),
            "committer": self.committer.as_dict(),
            "changes": [change.identity() for change in self.changes],
        }

    @property
    def sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.identity()))


class GitHubAppExactCommitPushRoute:
    """Create the exact local Git objects and fast-forward a ref via one App token."""

    route_id = "github_app_exact_commit_push_v1"

    def __init__(
        self,
        *,
        broker: InstallationTokenBroker,
        transport: GitHubJSONTransport,
        api_version: str = GITHUB_REST_API_VERSION,
        write_interval_seconds: float = 0.0,
        secondary_retry_delays: Sequence[float] = (),
        sleep: Callable[[float], None] = time.sleep,
        prefer_existing_blob_tree: bool = False,
    ) -> None:
        self.broker = broker
        self.transport = transport
        self.api_version = _identifier(api_version, field="api_version")
        self._replay: dict[str, tuple[str, dict[str, Any]]] = {}
        self._write_interval_seconds = float(write_interval_seconds)
        self._secondary_retry_delays = tuple(
            float(value) for value in secondary_retry_delays
        )
        self._sleep = sleep
        self._prefer_existing_blob_tree = bool(prefer_existing_blob_tree)
        self._last_write_at: float | None = None
        require(
            0.0 <= self._write_interval_seconds <= 5.0
            and len(self._secondary_retry_delays) <= 4
            and all(0.0 <= value <= 300.0 for value in self._secondary_retry_delays),
            "GITHUB_APP_WRITE_PACING_INVALID",
            "GitHub write pacing and retry delays must remain bounded.",
            status="BLOCKED",
        )

    def _pace_write(self) -> None:
        if self._write_interval_seconds <= 0.0:
            return
        now = time.monotonic()
        if self._last_write_at is not None:
            remaining = self._write_interval_seconds - (now - self._last_write_at)
            if remaining > 0.0:
                self._sleep(remaining)
        self._last_write_at = time.monotonic()

    @staticmethod
    def _oid_from_object(payload: Mapping[str, Any], *, field: str) -> str:
        nested = payload.get("object")
        require(
            isinstance(nested, Mapping),
            "GITHUB_APP_GIT_RESPONSE_INVALID",
            f"GitHub did not return the expected {field} object.",
            status="BLOCKED",
        )
        return _git_oid(cast(Mapping[str, Any], nested).get("sha"), field=field)

    def _request(
        self,
        *,
        token: str,
        method: str,
        path: str,
        body: Mapping[str, Any],
        expected_status: int,
    ) -> GitHubAPIResponse:
        exact_method = str(method).upper()
        response: GitHubAPIResponse | None = None
        for attempt in range(len(self._secondary_retry_delays) + 1):
            if exact_method in {"POST", "PATCH"}:
                self._pace_write()
            response = self.transport.request_json(
                method=exact_method,
                path=path,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": self.api_version,
                },
                body=body,
            )
            if response.status_code == expected_status:
                return response
            message = str(response.body.get("message") or "")[:500]
            lowered = message.casefold()
            secondary_limit = response.status_code in {403, 429} and any(
                marker in lowered
                for marker in (
                    "secondary rate limit",
                    "abuse detection",
                    "temporarily blocked",
                )
            )
            if secondary_limit and attempt < len(self._secondary_retry_delays):
                self._sleep(self._secondary_retry_delays[attempt])
                continue
            require(
                False,
                "GITHUB_APP_GIT_REQUEST_FAILED",
                "GitHub rejected an exact Git Database route request.",
                status="BLOCKED",
                method=exact_method,
                path=path,
                http_status=response.status_code,
                github_message=message,
                secondary_limit=secondary_limit,
                retry_attempt=attempt,
            )
        raise AssertionError("unreachable")

    def execute(
        self,
        request: ExactGitCommitPushRequest,
        *,
        token_request: InstallationTokenRequest,
        now: str,
    ) -> dict[str, Any]:
        prior = self._replay.get(request.idempotency_key)
        if prior is not None:
            require(
                prior[0] == request.sha256,
                "GITHUB_APP_PUSH_REPLAY_CONFLICT",
                "The GitHub App push idempotency key was reused for different bytes.",
                status="BLOCKED",
            )
            return {**prior[1], "idempotent_reuse": True}

        permissions = dict(token_request.permissions)
        workflow_change = any(
            change.path.startswith(".github/workflows/") for change in request.changes
        )
        require(
            token_request.project_id == request.project_id
            and token_request.task_id == request.task_id
            and token_request.repository == request.repository
            and permissions.get("contents") == "write"
            and (not workflow_change or permissions.get("workflows") == "write"),
            "GITHUB_APP_PUSH_AUTHORITY_MISMATCH",
            "The App token request lacks the exact task, repository, contents, or workflow authority.",
            status="BLOCKED",
            workflow_write_required=workflow_change,
        )
        token, token_receipt = self.broker.issue(token_request, now=now)
        owner, repository_name = request.repository.split("/", 1)
        repository_path = f"/repos/{quote(owner)}/{quote(repository_name)}"
        ref_name = f"heads/{request.branch}"
        ref_path = f"{repository_path}/git/ref/{quote(ref_name, safe='/')}"
        refs_path = f"{repository_path}/git/refs/{quote(ref_name, safe='/')}"
        request_ids: list[str] = []

        def record(response: GitHubAPIResponse) -> GitHubAPIResponse:
            if response.request_id:
                request_ids.append(
                    _identifier(response.request_id, field="github_request_id")
                )
            return response

        remote_before = record(
            self._request(
                token=token,
                method="GET",
                path=ref_path,
                body={},
                expected_status=200,
            )
        )
        remote_before_sha = self._oid_from_object(
            remote_before.body, field="remote_ref_sha"
        )
        if remote_before_sha == request.expected_commit_sha:
            existing_commit = record(
                self._request(
                    token=token,
                    method="GET",
                    path=(
                        f"{repository_path}/git/commits/"
                        f"{request.expected_commit_sha}"
                    ),
                    body={},
                    expected_status=200,
                )
            )
            existing_tree = existing_commit.body.get("tree")
            existing_parents = existing_commit.body.get("parents")
            expected_parents = [
                request.expected_parent_commit_sha,
                *request.additional_parent_commit_shas,
            ]
            require(
                isinstance(existing_tree, Mapping)
                and _git_oid(
                    cast(Mapping[str, Any], existing_tree).get("sha"),
                    field="existing_remote_tree_sha",
                )
                == request.expected_tree_sha
                and isinstance(existing_parents, list)
                and [
                    _git_oid(
                        cast(Mapping[str, Any], parent).get("sha"),
                        field="existing_remote_parent_sha",
                    )
                    for parent in existing_parents
                    if isinstance(parent, Mapping)
                ]
                == expected_parents,
                "GITHUB_APP_EXISTING_REMOTE_COMMIT_MISMATCH",
                "The existing remote commit does not match the exact local tree and parents.",
                status="MISMATCH",
            )
            receipt = _receipt(
                "evidence-lane.github-app-exact-commit-push-receipt.v1",
                status="PASS",
                route=self.route_id,
                request_sha256=request.sha256,
                token_broker_receipt_sha256=token_receipt["receipt_sha256"],
                project_id=request.project_id,
                task_id=request.task_id,
                repository=request.repository,
                branch=request.branch,
                remote_ref_before_commit_sha=remote_before_sha,
                remote_to_parent_fast_forward_verified=True,
                remote_to_parent_ahead_by=0,
                parent_commit_sha=request.expected_parent_commit_sha,
                parent_tree_sha=request.expected_parent_tree_sha,
                tree_sha=request.expected_tree_sha,
                commit_sha=request.expected_commit_sha,
                changed_path_count=len(request.changes),
                changed_path_set_sha256=sha256_bytes(
                    canonical_json_bytes([change.path for change in request.changes])
                ),
                github_request_ids=request_ids,
                source_write_authorized=True,
                workflow_write_authorized=workflow_change,
                commit_created=False,
                existing_blob_set_reused=True,
                ref_pushed=False,
                force_push=False,
                remote_ref_verified=True,
                credential_values_persisted=False,
                private_key_persisted=False,
                installation_token_persisted=False,
                candidate_created_or_accepted=False,
                pointer_moved=False,
                hil_inferred=False,
                recovered_after_exact_ref_update=True,
                idempotent_reuse=True,
            )
            require(
                not receipt_contains_secret(receipt),
                "GITHUB_APP_PUSH_RECEIPT_SECRET_BLOCKED",
                "The exact push recovery receipt contains a forbidden secret field.",
                status="BLOCKED",
            )
            self._replay[request.idempotency_key] = (request.sha256, receipt)
            return receipt
        remote_to_parent_ahead_by = 0
        if remote_before_sha != request.expected_parent_commit_sha:
            comparison = record(
                self._request(
                    token=token,
                    method="GET",
                    path=(
                        f"{repository_path}/compare/{quote(remote_before_sha)}..."
                        f"{quote(request.expected_parent_commit_sha)}"
                    ),
                    body={},
                    expected_status=200,
                )
            )
            merge_base = comparison.body.get("merge_base_commit")
            require(
                isinstance(merge_base, Mapping)
                and _git_oid(
                    cast(Mapping[str, Any], merge_base).get("sha"),
                    field="remote_to_parent_merge_base_sha",
                )
                == remote_before_sha
                and comparison.body.get("status") == "ahead"
                and int(comparison.body.get("ahead_by") or 0) >= 1
                and int(comparison.body.get("behind_by") or 0) == 0,
                "GITHUB_APP_REMOTE_FAST_FORWARD_DIVERGED",
                "The remote feature ref is not an ancestor of the exact local parent commit.",
                status="MISMATCH",
            )
            remote_to_parent_ahead_by = int(comparison.body.get("ahead_by") or 0)
        parent_commit = record(
            self._request(
                token=token,
                method="GET",
                path=(
                    f"{repository_path}/git/commits/"
                    f"{request.expected_parent_commit_sha}"
                ),
                body={},
                expected_status=200,
            )
        )
        parent_tree = parent_commit.body.get("tree")
        require(
            isinstance(parent_tree, Mapping)
            and _git_oid(
                cast(Mapping[str, Any], parent_tree).get("sha"),
                field="remote_parent_tree_sha",
            )
            == request.expected_parent_tree_sha,
            "GITHUB_APP_REMOTE_PARENT_TREE_MISMATCH",
            "The remote parent tree differs from the exact local parent tree.",
            status="MISMATCH",
        )

        tree_entries = [
            {
                "path": change.path,
                "mode": change.mode,
                "type": "blob",
                "sha": change.blob_sha if change.content is not None else None,
            }
            for change in request.changes
        ]
        tree_response: GitHubAPIResponse | None = None
        existing_blob_set_reused = False
        if self._prefer_existing_blob_tree:
            try:
                tree_response = record(
                    self._request(
                        token=token,
                        method="POST",
                        path=f"{repository_path}/git/trees",
                        body={
                            "base_tree": request.expected_parent_tree_sha,
                            "tree": tree_entries,
                        },
                        expected_status=201,
                    )
                )
                existing_blob_set_reused = True
            except EvidenceLaneError as exc:
                if int(exc.details.get("http_status") or 0) != 422:
                    raise

        if tree_response is None:
            tree_entries = []
            for change in request.changes:
                if change.content is None:
                    blob_sha = None
                else:
                    blob_response = record(
                        self._request(
                            token=token,
                            method="POST",
                            path=f"{repository_path}/git/blobs",
                            body={
                                "content": base64.b64encode(change.content).decode(
                                    "ascii"
                                ),
                                "encoding": "base64",
                            },
                            expected_status=201,
                        )
                    )
                    blob_sha = _git_oid(
                        blob_response.body.get("sha"),
                        field="created_blob_sha",
                    )
                    require(
                        blob_sha == change.blob_sha,
                        "GITHUB_APP_BLOB_IDENTITY_MISMATCH",
                        "GitHub created blob bytes that differ from the local Git object.",
                        status="MISMATCH",
                        path=change.path,
                    )
                tree_entries.append(
                    {
                        "path": change.path,
                        "mode": change.mode,
                        "type": "blob",
                        "sha": blob_sha,
                    }
                )

            tree_response = record(
                self._request(
                    token=token,
                    method="POST",
                    path=f"{repository_path}/git/trees",
                    body={
                        "base_tree": request.expected_parent_tree_sha,
                        "tree": tree_entries,
                    },
                    expected_status=201,
                )
            )
        created_tree_sha = _git_oid(
            tree_response.body.get("sha"),
            field="created_tree_sha",
        )
        require(
            created_tree_sha == request.expected_tree_sha,
            "GITHUB_APP_TREE_IDENTITY_MISMATCH",
            "GitHub created a tree that differs from the exact local tree.",
            status="MISMATCH",
        )
        commit_response = record(
            self._request(
                token=token,
                method="POST",
                path=f"{repository_path}/git/commits",
                body={
                    "message": request.commit_message,
                    "tree": request.expected_tree_sha,
                    "parents": [
                        request.expected_parent_commit_sha,
                        *request.additional_parent_commit_shas,
                    ],
                    "author": request.author.as_dict(),
                    "committer": request.committer.as_dict(),
                },
                expected_status=201,
            )
        )
        created_commit_sha = _git_oid(
            commit_response.body.get("sha"),
            field="created_commit_sha",
        )
        require(
            created_commit_sha == request.expected_commit_sha,
            "GITHUB_APP_COMMIT_IDENTITY_MISMATCH",
            "GitHub created a commit that differs from the exact local commit.",
            status="MISMATCH",
        )
        updated_ref = record(
            self._request(
                token=token,
                method="PATCH",
                path=refs_path,
                body={"sha": request.expected_commit_sha, "force": False},
                expected_status=200,
            )
        )
        require(
            self._oid_from_object(updated_ref.body, field="updated_ref_sha")
            == request.expected_commit_sha,
            "GITHUB_APP_REF_UPDATE_MISMATCH",
            "GitHub did not return the exact fast-forwarded ref identity.",
            status="MISMATCH",
        )
        remote_after = record(
            self._request(
                token=token,
                method="GET",
                path=ref_path,
                body={},
                expected_status=200,
            )
        )
        require(
            self._oid_from_object(remote_after.body, field="verified_ref_sha")
            == request.expected_commit_sha,
            "GITHUB_APP_REF_VERIFY_MISMATCH",
            "The post-push remote ref does not equal the exact local commit.",
            status="MISMATCH",
        )
        receipt = _receipt(
            "evidence-lane.github-app-exact-commit-push-receipt.v1",
            status="PASS",
            route=self.route_id,
            request_sha256=request.sha256,
            token_broker_receipt_sha256=token_receipt["receipt_sha256"],
            project_id=request.project_id,
            task_id=request.task_id,
            repository=request.repository,
            branch=request.branch,
            remote_ref_before_commit_sha=remote_before_sha,
            remote_to_parent_fast_forward_verified=True,
            remote_to_parent_ahead_by=remote_to_parent_ahead_by,
            parent_commit_sha=request.expected_parent_commit_sha,
            parent_tree_sha=request.expected_parent_tree_sha,
            tree_sha=request.expected_tree_sha,
            commit_sha=request.expected_commit_sha,
            changed_path_count=len(request.changes),
            changed_path_set_sha256=sha256_bytes(
                canonical_json_bytes([change.path for change in request.changes])
            ),
            github_request_ids=request_ids,
            source_write_authorized=True,
            workflow_write_authorized=workflow_change,
            commit_created=True,
            existing_blob_set_reused=existing_blob_set_reused,
            ref_pushed=True,
            force_push=False,
            remote_ref_verified=True,
            credential_values_persisted=False,
            private_key_persisted=False,
            installation_token_persisted=False,
            candidate_created_or_accepted=False,
            pointer_moved=False,
            hil_inferred=False,
            idempotent_reuse=False,
        )
        require(
            not receipt_contains_secret(receipt),
            "GITHUB_APP_PUSH_RECEIPT_SECRET_BLOCKED",
            "The exact push receipt contains a forbidden secret field.",
            status="BLOCKED",
        )
        self._replay[request.idempotency_key] = (request.sha256, receipt)
        return receipt


@dataclass(frozen=True, slots=True)
class GitHubAppMainFastForwardRequest:
    """Exact green feature head authorized for one non-force main fast-forward."""

    request_id: str
    idempotency_key: str
    project_id: str
    task_id: str
    repository: str
    source_branch: str
    target_branch: str
    expected_source_commit_sha: str
    expected_source_tree_sha: str
    expected_target_commit_sha: str
    required_workflow_names: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        idempotency_key: str,
        project_id: str,
        task_id: str,
        repository: str,
        source_branch: str,
        target_branch: str,
        expected_source_commit_sha: str,
        expected_source_tree_sha: str,
        expected_target_commit_sha: str,
        required_workflow_names: Sequence[str],
    ) -> GitHubAppMainFastForwardRequest:
        source = _git_branch(source_branch)
        target = _git_branch(target_branch)
        workflows = tuple(
            sorted(
                {
                    _bounded_text(name, field="required_workflow_name")
                    for name in required_workflow_names
                }
            )
        )
        require(
            target == "main" and source != target and 1 <= len(workflows) <= 16,
            "GITHUB_APP_MAIN_FAST_FORWARD_BOUNDARY_INVALID",
            "The App fast-forward route requires one non-main source and bounded green workflow gate.",
            status="BLOCKED",
        )
        return cls(
            request_id=_identifier(request_id, field="request_id"),
            idempotency_key=_identifier(idempotency_key, field="idempotency_key"),
            project_id=_identifier(project_id, field="project_id"),
            task_id=_identifier(task_id, field="task_id"),
            repository=_repository(repository),
            source_branch=source,
            target_branch=target,
            expected_source_commit_sha=_git_oid(
                expected_source_commit_sha,
                field="expected_source_commit_sha",
            ),
            expected_source_tree_sha=_git_oid(
                expected_source_tree_sha,
                field="expected_source_tree_sha",
            ),
            expected_target_commit_sha=_git_oid(
                expected_target_commit_sha,
                field="expected_target_commit_sha",
            ),
            required_workflow_names=workflows,
        )

    def identity(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "repository": self.repository,
            "source_branch": self.source_branch,
            "target_branch": self.target_branch,
            "expected_source_commit_sha": self.expected_source_commit_sha,
            "expected_source_tree_sha": self.expected_source_tree_sha,
            "expected_target_commit_sha": self.expected_target_commit_sha,
            "required_workflow_names": list(self.required_workflow_names),
        }

    @property
    def sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.identity()))


class GitHubAppMainFastForwardRoute:
    """Fast-forward main to one exact green feature head without replaying blobs."""

    route_id = "github_app_main_fast_forward_v3"

    def __init__(
        self,
        *,
        broker: InstallationTokenBroker,
        transport: GitHubJSONTransport,
        api_version: str = GITHUB_REST_API_VERSION,
    ) -> None:
        self.broker = broker
        self.transport = transport
        self.api_version = _identifier(api_version, field="api_version")
        self._replay: dict[str, tuple[str, dict[str, Any]]] = {}

    def _request(
        self,
        *,
        token: str,
        method: str,
        path: str,
        body: Mapping[str, Any],
        expected_status: int,
    ) -> GitHubAPIResponse:
        response = self.transport.request_json(
            method=method,
            path=path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": self.api_version,
            },
            body=body,
        )
        require(
            response.status_code == expected_status,
            "GITHUB_APP_MAIN_FAST_FORWARD_REQUEST_FAILED",
            "GitHub rejected the governed feature-to-main fast-forward request.",
            status="BLOCKED",
            method=method,
            path=path.split("?", 1)[0],
            http_status=response.status_code,
            github_message=str(response.body.get("message") or "")[:256],
        )
        return response

    @staticmethod
    def _ref_oid(response: GitHubAPIResponse, *, field: str) -> str:
        nested = response.body.get("object")
        require(
            isinstance(nested, Mapping),
            "GITHUB_APP_MAIN_FAST_FORWARD_RESPONSE_INVALID",
            "GitHub did not return the expected exact ref object.",
            status="BLOCKED",
            field=field,
        )
        return _git_oid(cast(Mapping[str, Any], nested).get("sha"), field=field)

    @staticmethod
    def _commit_tree(response: GitHubAPIResponse, *, field: str) -> str:
        commit = response.body.get("commit")
        tree = commit.get("tree") if isinstance(commit, Mapping) else None
        require(
            isinstance(tree, Mapping),
            "GITHUB_APP_MAIN_FAST_FORWARD_RESPONSE_INVALID",
            "GitHub did not return the expected commit tree identity.",
            status="BLOCKED",
            field=field,
        )
        return _git_oid(cast(Mapping[str, Any], tree).get("sha"), field=field)

    @staticmethod
    def _author_login(response: GitHubAPIResponse, *, field: str) -> str:
        author = response.body.get("author")
        require(
            isinstance(author, Mapping),
            "GITHUB_APP_MAIN_FAST_FORWARD_RESPONSE_INVALID",
            "GitHub did not return the source commit App author.",
            status="BLOCKED",
            field=field,
        )
        return _bounded_text(
            str(cast(Mapping[str, Any], author).get("login") or ""),
            field=field,
        )

    def execute(
        self,
        request: GitHubAppMainFastForwardRequest,
        *,
        token_request: InstallationTokenRequest,
        now: str,
    ) -> dict[str, Any]:
        prior = self._replay.get(request.idempotency_key)
        if prior is not None:
            require(
                prior[0] == request.sha256,
                "GITHUB_APP_MAIN_FAST_FORWARD_REPLAY_CONFLICT",
                "The main fast-forward idempotency key was reused for different authority.",
                status="BLOCKED",
            )
            return {**prior[1], "idempotent_reuse": True}

        permissions = dict(token_request.permissions)
        require(
            token_request.project_id == request.project_id
            and token_request.task_id == request.task_id
            and token_request.repository == request.repository
            and permissions.get("metadata") == "read"
            and permissions.get("actions") == "read"
            and permissions.get("contents") == "write",
            "GITHUB_APP_MAIN_FAST_FORWARD_AUTHORITY_MISMATCH",
            "The App token lacks exact task, repository, CI-read, or contents-write authority.",
            status="BLOCKED",
        )
        token, token_receipt = self.broker.issue(token_request, now=now)
        owner, repository_name = request.repository.split("/", 1)
        repository_path = f"/repos/{quote(owner)}/{quote(repository_name)}"
        source_ref_path = (
            f"{repository_path}/git/ref/"
            f"{quote(f'heads/{request.source_branch}', safe='/')}"
        )
        target_ref_path = (
            f"{repository_path}/git/ref/"
            f"{quote(f'heads/{request.target_branch}', safe='/')}"
        )
        target_refs_path = (
            f"{repository_path}/git/refs/"
            f"{quote(f'heads/{request.target_branch}', safe='/')}"
        )
        request_ids: list[str] = []

        def record(response: GitHubAPIResponse) -> GitHubAPIResponse:
            if response.request_id:
                request_ids.append(
                    _identifier(response.request_id, field="github_request_id")
                )
            return response

        target_before = record(
            self._request(
                token=token,
                method="GET",
                path=target_ref_path,
                body={},
                expected_status=200,
            )
        )
        require(
            self._ref_oid(target_before, field="target_ref_before_sha")
            == request.expected_target_commit_sha,
            "GITHUB_APP_MAIN_FAST_FORWARD_TARGET_MOVED",
            "The remote main ref moved after fast-forward authorization.",
            status="MISMATCH",
        )
        source_ref = record(
            self._request(
                token=token,
                method="GET",
                path=source_ref_path,
                body={},
                expected_status=200,
            )
        )
        require(
            self._ref_oid(source_ref, field="source_ref_sha")
            == request.expected_source_commit_sha,
            "GITHUB_APP_MAIN_FAST_FORWARD_SOURCE_MOVED",
            "The governed feature branch moved after its green workflow gate.",
            status="MISMATCH",
        )
        source_commit = record(
            self._request(
                token=token,
                method="GET",
                path=(
                    f"{repository_path}/commits/{request.expected_source_commit_sha}"
                ),
                body={},
                expected_status=200,
            )
        )
        require(
            self._commit_tree(source_commit, field="source_tree_sha")
            == request.expected_source_tree_sha,
            "GITHUB_APP_MAIN_FAST_FORWARD_SOURCE_TREE_MISMATCH",
            "The green source commit tree differs from the governed local identity.",
            status="MISMATCH",
        )
        require(
            self._author_login(source_commit, field="source_commit_author_login")
            == "evidence-lane[bot]",
            "GITHUB_APP_MAIN_FAST_FORWARD_BOT_ACTOR_MISMATCH",
            "The governed feature commit was not authored by the Evidence Lane App bot.",
            status="MISMATCH",
        )
        comparison = record(
            self._request(
                token=token,
                method="GET",
                path=(
                    f"{repository_path}/compare/"
                    f"{quote(request.expected_target_commit_sha)}..."
                    f"{quote(request.expected_source_commit_sha)}"
                ),
                body={},
                expected_status=200,
            )
        )
        merge_base = comparison.body.get("merge_base_commit")
        require(
            isinstance(merge_base, Mapping)
            and _git_oid(
                cast(Mapping[str, Any], merge_base).get("sha"),
                field="merge_base_commit_sha",
            )
            == request.expected_target_commit_sha
            and comparison.body.get("status") == "ahead"
            and int(comparison.body.get("ahead_by") or 0) >= 1
            and int(comparison.body.get("behind_by") or 0) == 0,
            "GITHUB_APP_MAIN_FAST_FORWARD_DIVERGED",
            "The green feature head is not a strict descendant of the current main head.",
            status="MISMATCH",
        )

        workflow_response = record(
            self._request(
                token=token,
                method="GET",
                path=(
                    f"{repository_path}/actions/runs?head_sha="
                    f"{quote(request.expected_source_commit_sha)}&per_page=100"
                ),
                body={},
                expected_status=200,
            )
        )
        workflow_runs = workflow_response.body.get("workflow_runs")
        require(
            isinstance(workflow_runs, Sequence)
            and not isinstance(workflow_runs, (str, bytes)),
            "GITHUB_APP_MAIN_FAST_FORWARD_WORKFLOW_RESPONSE_INVALID",
            "GitHub did not return the exact-head workflow run list.",
            status="BLOCKED",
        )
        latest: dict[str, tuple[tuple[int, int], Mapping[str, Any]]] = {}
        for raw_run in cast(Sequence[object], workflow_runs):
            if not isinstance(raw_run, Mapping):
                continue
            run = cast(Mapping[str, Any], raw_run)
            if (
                str(run.get("head_sha") or "").lower()
                != request.expected_source_commit_sha
            ):
                continue
            name = str(run.get("name") or "").strip()
            if name not in request.required_workflow_names:
                continue
            try:
                rank = (int(run.get("run_number") or 0), int(run.get("id") or 0))
            except (TypeError, ValueError):
                rank = (0, 0)
            if name not in latest or rank > latest[name][0]:
                latest[name] = (rank, run)
        workflow_gate: dict[str, dict[str, Any]] = {}
        for name in request.required_workflow_names:
            selected = latest.get(name)
            require(
                selected is not None,
                "GITHUB_APP_MAIN_FAST_FORWARD_BRANCH_GATE_NOT_GREEN",
                "A required exact-head workflow run is missing.",
                status="BLOCKED", workflow=name,
            )
            assert selected is not None
            run = selected[1]
            require(
                run.get("status") == "completed" and run.get("conclusion") == "success",
                "GITHUB_APP_MAIN_FAST_FORWARD_BRANCH_GATE_NOT_GREEN",
                "A required exact-head workflow run is not successful.",
                status="BLOCKED", workflow=name,
                workflow_status=run.get("status"),
                workflow_conclusion=run.get("conclusion"),
            )
            workflow_gate[name] = {
                "id": int(run.get("id") or 0),
                "run_number": int(run.get("run_number") or 0),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "head_sha": request.expected_source_commit_sha,
            }

        updated_ref = record(
            self._request(
                token=token,
                method="PATCH",
                path=target_refs_path,
                body={"sha": request.expected_source_commit_sha, "force": False},
                expected_status=200,
            )
        )
        require(
            self._ref_oid(updated_ref, field="updated_main_ref_sha")
            == request.expected_source_commit_sha,
            "GITHUB_APP_MAIN_FAST_FORWARD_REF_UPDATE_MISMATCH",
            "GitHub did not return the exact fast-forwarded main ref identity.",
            status="MISMATCH",
        )
        target_after = record(
            self._request(
                token=token,
                method="GET",
                path=target_ref_path,
                body={},
                expected_status=200,
            )
        )
        require(
            self._ref_oid(target_after, field="target_ref_after_sha")
            == request.expected_source_commit_sha,
            "GITHUB_APP_MAIN_FAST_FORWARD_REF_VERIFY_MISMATCH",
            "The post-update main ref does not equal the green feature commit.",
            status="MISMATCH",
        )
        verified_commit = record(
            self._request(
                token=token,
                method="GET",
                path=f"{repository_path}/commits/{request.expected_source_commit_sha}",
                body={},
                expected_status=200,
            )
        )
        require(
            self._commit_tree(verified_commit, field="verified_main_tree_sha")
            == request.expected_source_tree_sha
            and self._author_login(
                verified_commit,
                field="verified_main_author_login",
            )
            == "evidence-lane[bot]",
            "GITHUB_APP_MAIN_FAST_FORWARD_POST_VERIFY_MISMATCH",
            "The persisted main commit differs from the green App-authored feature head.",
            status="MISMATCH",
        )
        receipt = _receipt(
            "evidence-lane.github-app-main-fast-forward.v1",
            status="PASS",
            route=self.route_id,
            action="FAST_FORWARD_MAIN",
            request_sha256=request.sha256,
            token_broker_receipt_sha256=token_receipt["receipt_sha256"],
            project_id=request.project_id,
            task_id=request.task_id,
            repository=request.repository,
            promotion={
                "source_branch": request.source_branch,
                "target_branch": request.target_branch,
                "target_before_commit": request.expected_target_commit_sha,
                "source_commit": request.expected_source_commit_sha,
                "main_commit": request.expected_source_commit_sha,
                "main_tree": request.expected_source_tree_sha,
                "merge_base_commit": request.expected_target_commit_sha,
                "ahead_by": int(comparison.body.get("ahead_by") or 0),
                "behind_by": int(comparison.body.get("behind_by") or 0),
            },
            branch_workflow_gate=workflow_gate,
            repository_identity={
                "branch": request.target_branch,
                "commit_sha": request.expected_source_commit_sha,
                "tree_sha": request.expected_source_tree_sha,
            },
            authorization={
                "policy": "GOVERNED_FEATURE_TO_MAIN_FAST_FORWARD",
                "direct_main_implementation_authorized": False,
                "merge_authorized": False,
                "fast_forward_authorized": True,
            },
            source_tree_reused=True,
            blob_reupload_count=0,
            direct_ref_patch_used=True,
            force_push=False,
            github_commit_author_login="evidence-lane[bot]",
            github_request_ids=request_ids,
            credential_values_persisted=False,
            private_key_persisted=False,
            installation_token_persisted=False,
            candidate_created_or_accepted=False,
            pointer_moved=False,
            hil_inferred=False,
            idempotent_reuse=False,
        )
        require(
            not receipt_contains_secret(receipt),
            "GITHUB_APP_MAIN_FAST_FORWARD_RECEIPT_SECRET_BLOCKED",
            "The main fast-forward receipt contains a forbidden secret field.",
            status="BLOCKED",
        )
        self._replay[request.idempotency_key] = (request.sha256, receipt)
        return receipt


class WebhookVerifier:
    """Authenticate bytes before payload parsing and make delivery replay explicit."""

    def __init__(
        self,
        *,
        webhook_secret: bytes,
        allowed_events: Sequence[str],
        max_age_seconds: int = 300,
    ) -> None:
        require(
            len(webhook_secret) >= 16,
            "GITHUB_APP_WEBHOOK_SECRET_INVALID",
            "Webhook verification requires a non-empty in-memory secret.",
            status="BLOCKED",
        )
        events = tuple(sorted(set(allowed_events)))
        require(
            bool(events) and set(events) <= _ALLOWED_EVENTS,
            "GITHUB_APP_WEBHOOK_EVENT_INVALID",
            "The webhook verifier event allowlist is invalid.",
            status="BLOCKED",
        )
        self._secret = bytes(webhook_secret)
        self.allowed_events = events
        self.max_age_seconds = max_age_seconds
        self._deliveries: dict[str, tuple[str, dict[str, Any]]] = {}

    def verify(
        self,
        *,
        delivery_id: str,
        event: str,
        body: bytes,
        signature: str,
        delivered_at: str,
        now: str,
    ) -> tuple[Mapping[str, Any], dict[str, Any]]:
        exact_delivery = _identifier(delivery_id, field="delivery_id")
        match = _SIGNATURE.fullmatch(signature.strip())
        expected = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        require(
            match is not None and hmac.compare_digest(match.group(1).lower(), expected),
            "GITHUB_APP_WEBHOOK_SIGNATURE_INVALID",
            "The webhook signature failed before payload authority parsing.",
            status="BLOCKED",
            payload_parsed=False,
        )
        require(
            event in self.allowed_events,
            "GITHUB_APP_WEBHOOK_EVENT_BLOCKED",
            "The signed webhook event is outside the manifest allowlist.",
            status="BLOCKED",
        )
        exact_now = _timestamp(now, field="now")
        exact_delivered = _timestamp(delivered_at, field="delivered_at")
        require(
            abs((exact_now - exact_delivered).total_seconds()) <= self.max_age_seconds,
            "GITHUB_APP_WEBHOOK_STALE",
            "The webhook delivery timestamp is outside the replay window.",
            status="BLOCKED",
        )
        body_sha = sha256_bytes(body)
        prior = self._deliveries.get(exact_delivery)
        if prior is not None:
            require(
                prior[0] == body_sha,
                "GITHUB_APP_WEBHOOK_REPLAY_CONFLICT",
                "A delivery identity was replayed with different bytes.",
                status="BLOCKED",
            )
            return {}, {**prior[1], "status": "IDEMPOTENT_REPLAY"}
        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            require(
                False,
                "GITHUB_APP_WEBHOOK_PAYLOAD_INVALID",
                "The authenticated webhook body is not valid JSON.",
                status="BLOCKED",
                error=str(exc),
            )
        require(
            isinstance(decoded, Mapping),
            "GITHUB_APP_WEBHOOK_PAYLOAD_INVALID",
            "The authenticated webhook payload must be a JSON object.",
            status="BLOCKED",
        )
        receipt = _receipt(
            "evidence-lane.github-app-webhook-receipt.v1",
            status="PASS",
            delivery_id=exact_delivery,
            event=event,
            body_sha256=body_sha,
            signature_valid=True,
            payload_parsed_after_signature=True,
            webhook_secret_persisted=False,
            authority_effect="NONE",
            pointer_moved=False,
            hil_inferred=False,
        )
        self._deliveries[exact_delivery] = (body_sha, receipt)
        return cast(Mapping[str, Any], decoded), receipt


class GitHubWebhookHandler(Protocol):
    """Bounded post-authentication event handler seam."""

    handler_id: str

    def handle(
        self, *, event: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


class GitHubWebhookRoute:
    """Exact public webhook route with authentication and replay isolation."""

    def __init__(
        self,
        *,
        verifier: WebhookVerifier,
        handler: GitHubWebhookHandler,
        max_body_bytes: int = 1_048_576,
        max_handler_result_bytes: int = 65_536,
    ) -> None:
        require(
            0 < max_body_bytes <= 10_485_760,
            "GITHUB_APP_WEBHOOK_BODY_BOUND_INVALID",
            "The GitHub webhook body bound is invalid.",
            status="BLOCKED",
        )
        require(
            0 < max_handler_result_bytes <= 1_048_576,
            "GITHUB_APP_WEBHOOK_RESULT_BOUND_INVALID",
            "The GitHub webhook handler-result bound is invalid.",
            status="BLOCKED",
        )
        self.verifier = verifier
        self.handler = handler
        self.max_body_bytes = max_body_bytes
        self.max_handler_result_bytes = max_handler_result_bytes
        self._route_receipts: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _headers(headers: Mapping[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_name, raw_value in headers.items():
            name = str(raw_name).strip().lower()
            require(
                bool(name) and name not in normalized,
                "GITHUB_APP_WEBHOOK_HEADERS_INVALID",
                "Webhook headers must have unique case-insensitive names.",
                status="BLOCKED",
            )
            value = str(raw_value).strip()
            require(
                "\r" not in value and "\n" not in value and "\x00" not in value,
                "GITHUB_APP_WEBHOOK_HEADERS_INVALID",
                "Webhook headers contain an invalid value.",
                status="BLOCKED",
            )
            normalized[name] = value
        return normalized

    def dispatch(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        received_at: str,
        now: str,
    ) -> dict[str, Any]:
        require(
            method.strip().upper() == "POST" and path == GITHUB_APP_WEBHOOK_ROUTE,
            "GITHUB_APP_WEBHOOK_ROUTE_INVALID",
            "The request does not match the exact GitHub App webhook route.",
            status="BLOCKED",
        )
        exact_headers = self._headers(headers)
        content_type = exact_headers.get("content-type", "").split(";", 1)[0].lower()
        require(
            content_type == "application/json",
            "GITHUB_APP_WEBHOOK_CONTENT_TYPE_INVALID",
            "The GitHub App webhook route requires application/json.",
            status="BLOCKED",
        )
        require(
            0 < len(body) <= self.max_body_bytes,
            "GITHUB_APP_WEBHOOK_BODY_BOUND_EXCEEDED",
            "The GitHub App webhook body is empty or exceeds its bounded route.",
            status="BLOCKED",
            body_bytes=len(body),
            max_body_bytes=self.max_body_bytes,
        )
        required_headers = {
            "delivery_id": exact_headers.get("x-github-delivery", ""),
            "event": exact_headers.get("x-github-event", ""),
            "signature": exact_headers.get("x-hub-signature-256", ""),
        }
        require(
            all(required_headers.values()),
            "GITHUB_APP_WEBHOOK_HEADERS_REQUIRED",
            "The GitHub App webhook request is missing an authentication header.",
            status="BLOCKED",
        )
        delivery_id = required_headers["delivery_id"]
        payload, verification = self.verifier.verify(
            delivery_id=delivery_id,
            event=required_headers["event"],
            body=body,
            signature=required_headers["signature"],
            delivered_at=received_at,
            now=now,
        )
        if verification["status"] == "IDEMPOTENT_REPLAY":
            prior = self._route_receipts.get(delivery_id)
            require(
                prior is not None,
                "GITHUB_APP_WEBHOOK_HANDLER_INCOMPLETE",
                "The authenticated delivery has no completed handler receipt.",
                status="BLOCKED",
            )
            assert prior is not None
            return _receipt(
                "evidence-lane.github-app-webhook-route-replay-receipt.v1",
                status="IDEMPOTENT_REPLAY",
                delivery_id=delivery_id,
                event=required_headers["event"],
                original_route_receipt_sha256=prior["receipt_sha256"],
                handler_reinvoked=False,
                authority_effect="NONE",
                pointer_moved=False,
                hil_inferred=False,
            )
        result = self.handler.handle(event=required_headers["event"], payload=payload)
        require(
            isinstance(result, Mapping),
            "GITHUB_APP_WEBHOOK_HANDLER_RESULT_INVALID",
            "The authenticated GitHub webhook handler returned an invalid result.",
            status="BLOCKED",
        )
        result_bytes = canonical_json_bytes(result)
        require(
            len(result_bytes) <= self.max_handler_result_bytes
            and not receipt_contains_secret(result),
            "GITHUB_APP_WEBHOOK_HANDLER_RESULT_UNSAFE",
            "The authenticated GitHub webhook handler result is oversized or secret-bearing.",
            status="BLOCKED",
        )
        receipt = _receipt(
            "evidence-lane.github-app-webhook-route-receipt.v1",
            status="PASS",
            route=GITHUB_APP_WEBHOOK_ROUTE,
            delivery_id=delivery_id,
            event=required_headers["event"],
            body_sha256=verification["body_sha256"],
            signature_valid=True,
            handler_id=_identifier(self.handler.handler_id, field="handler_id"),
            handler_result_sha256=sha256_bytes(result_bytes),
            handler_result_keys=len(result),
            handler_reinvoked=False,
            raw_payload_persisted=False,
            handler_result_persisted=False,
            webhook_secret_persisted=False,
            authority_effect="NONE",
            pointer_moved=False,
            hil_inferred=False,
        )
        self._route_receipts[delivery_id] = receipt
        return receipt


def map_check_run_receipt(
    *,
    project_id: str,
    task_id: str,
    repository: str,
    check_run_id: str,
    status: str,
    conclusion: str | None,
) -> dict[str, Any]:
    """Map check state without granting candidate, HIL, Fuse, or pointer authority."""

    exact_status = status.strip().lower()
    require(
        exact_status in {"queued", "in_progress", "completed"},
        "GITHUB_APP_CHECK_STATUS_INVALID",
        "The check-run status is unsupported.",
        status="BLOCKED",
    )
    exact_conclusion = conclusion.strip().lower() if conclusion else None
    allowed_conclusions = {
        "action_required",
        "cancelled",
        "failure",
        "neutral",
        "skipped",
        "stale",
        "success",
        "timed_out",
    }
    require(
        (exact_status == "completed" and exact_conclusion in allowed_conclusions)
        or (exact_status != "completed" and exact_conclusion is None),
        "GITHUB_APP_CHECK_CONCLUSION_INVALID",
        "Check conclusion does not match the check-run state.",
        status="BLOCKED",
    )
    return _receipt(
        "evidence-lane.github-app-check-run-receipt.v1",
        status="PASS",
        project_id=_identifier(project_id, field="project_id"),
        task_id=_identifier(task_id, field="task_id"),
        repository=_repository(repository),
        check_run_id=_identifier(check_run_id, field="check_run_id"),
        check_status=exact_status,
        conclusion=exact_conclusion,
        project_truth_effect="NONE",
        learning_effect="NONE",
        canon_effect="NONE",
        candidate_accepted=False,
        fuse_invoked=False,
        pointer_moved=False,
        hil_inferred=False,
    )


@dataclass(frozen=True, slots=True)
class ProductionDeliveryIdentity:
    """Exact, credential-free identity for one GitHub-delivered Codex build.

    The identity is intentionally narrower than a release authority.  It proves
    that one GitHub App installation, repository ref, successful Actions run,
    package, and branch-commit recovery slot agree.  It cannot create a commit,
    push a ref, promote a PV, infer HIL, or mutate the main-merge fallback.
    """

    delivery_id: str
    installation_binding_sha256: str
    project_id: str
    task_id: str
    accepted_pv: str
    repository: str
    branch: str
    commit_sha: str
    tree_sha: str
    actions_run_id: str
    actions_head_sha: str
    package_id: str
    package_version: str
    package_sha256: str
    package_source_commit: str
    mutable_local_slot: str
    branch_commit_slot: str
    main_merge_fallback_slot: str
    installed_version: str
    installed_package_sha256: str
    installed_surface_sha256: str
    main_merge_fallback_before_sha256: str
    main_merge_fallback_after_sha256: str

    @classmethod
    def create(
        cls,
        *,
        delivery_id: str,
        installation_binding: InstallationBinding,
        repository: str,
        branch: str,
        commit_sha: str,
        tree_sha: str,
        actions_run_id: str,
        actions_status: str,
        actions_conclusion: str,
        actions_head_sha: str,
        package_id: str,
        package_version: str,
        package_sha256: str,
        package_source_commit: str,
        mutable_local_slot: str,
        branch_commit_slot: str,
        main_merge_fallback_slot: str,
        installed_version: str,
        installed_package_sha256: str,
        installed_surface_sha256: str,
        main_merge_fallback_before_sha256: str,
        main_merge_fallback_after_sha256: str,
    ) -> ProductionDeliveryIdentity:
        exact_repository = _repository(repository)
        exact_commit = _git_oid(commit_sha, field="commit_sha")
        exact_actions_head = _git_oid(actions_head_sha, field="actions_head_sha")
        exact_package_commit = _git_oid(
            package_source_commit, field="package_source_commit"
        )
        exact_branch = _bounded_text(branch, field="branch")
        require(
            exact_repository in installation_binding.repositories,
            "GITHUB_APP_DELIVERY_REPOSITORY_MISMATCH",
            "The delivery repository is outside the exact installation binding.",
            status="BLOCKED",
        )
        require(
            actions_status.strip().lower() == "completed"
            and actions_conclusion.strip().lower() == "success",
            "GITHUB_APP_DELIVERY_ACTIONS_NOT_GREEN",
            "Production delivery requires one completed successful Actions run.",
            status="BLOCKED",
        )
        require(
            exact_actions_head == exact_commit == exact_package_commit,
            "GITHUB_APP_DELIVERY_COMMIT_MISMATCH",
            "The ref, Actions run, and exact package must bind the same commit.",
            status="MISMATCH",
        )
        exact_package_sha = _sha256(package_sha256, field="package_sha256")
        require(
            _sha256(installed_package_sha256, field="installed_package_sha256")
            == exact_package_sha,
            "GITHUB_APP_DELIVERY_PACKAGE_INSTALL_MISMATCH",
            "The installed branch-commit slot must contain the exact delivered package.",
            status="MISMATCH",
        )
        exact_version = _bounded_text(package_version, field="package_version")
        require(
            _bounded_text(installed_version, field="installed_version")
            == exact_version,
            "GITHUB_APP_DELIVERY_VERSION_INSTALL_MISMATCH",
            "The installed branch-commit slot version must equal the package version.",
            status="MISMATCH",
        )
        slots = (
            _bounded_text(mutable_local_slot, field="mutable_local_slot"),
            _bounded_text(branch_commit_slot, field="branch_commit_slot"),
            _bounded_text(main_merge_fallback_slot, field="main_merge_fallback_slot"),
        )
        require(
            len(set(slots)) == 3,
            "GITHUB_APP_DELIVERY_SLOT_ALIAS_BLOCKED",
            "Mutable-local, branch-commit, and main-merge slots must be distinct.",
            status="BLOCKED",
        )
        fallback_before = _sha256(
            main_merge_fallback_before_sha256,
            field="main_merge_fallback_before_sha256",
        )
        fallback_after = _sha256(
            main_merge_fallback_after_sha256,
            field="main_merge_fallback_after_sha256",
        )
        require(
            fallback_before == fallback_after,
            "GITHUB_APP_DELIVERY_MAIN_FALLBACK_MUTATED",
            "The branch checkpoint route must leave the main-merge fallback unchanged.",
            status="MISMATCH",
        )
        return cls(
            delivery_id=_identifier(delivery_id, field="delivery_id"),
            installation_binding_sha256=installation_binding.sha256,
            project_id=installation_binding.project_id,
            task_id=installation_binding.task_id,
            accepted_pv=installation_binding.accepted_pv,
            repository=exact_repository,
            branch=exact_branch,
            commit_sha=exact_commit,
            tree_sha=_git_oid(tree_sha, field="tree_sha"),
            actions_run_id=_identifier(actions_run_id, field="actions_run_id"),
            actions_head_sha=exact_actions_head,
            package_id=_identifier(package_id, field="package_id"),
            package_version=exact_version,
            package_sha256=exact_package_sha,
            package_source_commit=exact_package_commit,
            mutable_local_slot=slots[0],
            branch_commit_slot=slots[1],
            main_merge_fallback_slot=slots[2],
            installed_version=exact_version,
            installed_package_sha256=exact_package_sha,
            installed_surface_sha256=_sha256(
                installed_surface_sha256, field="installed_surface_sha256"
            ),
            main_merge_fallback_before_sha256=fallback_before,
            main_merge_fallback_after_sha256=fallback_after,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "evidence-lane.github-app-production-delivery-identity.v1",
            "delivery_id": self.delivery_id,
            "installation_binding_sha256": self.installation_binding_sha256,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "accepted_pv": self.accepted_pv,
            "source": {
                "repository": self.repository,
                "branch": self.branch,
                "commit_sha": self.commit_sha,
                "tree_sha": self.tree_sha,
            },
            "actions": {
                "run_id": self.actions_run_id,
                "status": "completed",
                "conclusion": "success",
                "head_sha": self.actions_head_sha,
            },
            "package": {
                "package_id": self.package_id,
                "version": self.package_version,
                "sha256": self.package_sha256,
                "source_commit": self.package_source_commit,
            },
            "slots": {
                "mutable_local": self.mutable_local_slot,
                "branch_commit_recovery": self.branch_commit_slot,
                "main_merge_fallback": self.main_merge_fallback_slot,
            },
            "installation": {
                "version": self.installed_version,
                "package_sha256": self.installed_package_sha256,
                "surface_sha256": self.installed_surface_sha256,
            },
            "main_merge_fallback": {
                "before_sha256": self.main_merge_fallback_before_sha256,
                "after_sha256": self.main_merge_fallback_after_sha256,
                "unchanged": True,
            },
        }

    @property
    def sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.as_dict()))


class GitHubAppProductionDeliveryRoute:
    """Replay-safe public SDK seam for exact checkpoint delivery evidence."""

    route_id = "github_app_production_delivery_v1"

    def __init__(self) -> None:
        self._receipts: dict[str, dict[str, Any]] = {}

    def seal(self, identity: ProductionDeliveryIdentity) -> dict[str, Any]:
        body = {
            "schema": "evidence-lane.github-app-production-delivery-receipt.v1",
            "status": "PASS",
            "route": self.route_id,
            "identity": identity.as_dict(),
            "identity_sha256": identity.sha256,
            "credential_values_persisted": False,
            "private_key_loaded": False,
            "installation_token_persisted": False,
            "commit_created": False,
            "ref_pushed": False,
            "installation_performed_by_contract": False,
            "candidate_created_or_accepted": False,
            "pointer_moved": False,
            "hil_inferred": False,
        }
        receipt = {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}
        prior = self._receipts.get(identity.delivery_id)
        require(
            prior is None or prior == receipt,
            "GITHUB_APP_DELIVERY_REPLAY_CONFLICT",
            "The delivery identity was replayed with different exact bytes.",
            status="BLOCKED",
        )
        if prior is not None:
            return prior
        self._receipts[identity.delivery_id] = receipt
        return receipt


@dataclass(frozen=True, slots=True)
class TesterRequest:
    request_id: str
    tester_subject_sha256: str
    requested_scope: str
    requested_at: str

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        tester_subject: str,
        requested_scope: str,
        requested_at: str,
    ) -> TesterRequest:
        _timestamp(requested_at, field="requested_at")
        require(
            requested_scope == "SIGNED_INSTALLER_ARTIFACT_ONLY",
            "GITHUB_APP_TESTER_SCOPE_OVERBROAD",
            "Tester access is artifact-only and cannot expose the development repository.",
            status="BLOCKED",
        )
        normalized_subject = tester_subject.strip()
        require(
            bool(normalized_subject),
            "GITHUB_APP_TESTER_IDENTITY_REQUIRED",
            "A tester request requires one host-verified subject.",
            status="BLOCKED",
        )
        return cls(
            request_id=_identifier(request_id, field="request_id"),
            tester_subject_sha256=sha256_bytes(normalized_subject.encode("utf-8")),
            requested_scope=requested_scope,
            requested_at=requested_at,
        )


@dataclass(frozen=True, slots=True)
class ArtifactEntitlement:
    entitlement_id: str
    request_id: str
    tester_subject_sha256: str
    artifact_id: str
    artifact_sha256: str
    terms_sha256: str
    approved_by: str
    approved_at: str
    expires_at: str
    revoked: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "evidence-lane.github-app-artifact-entitlement.v1",
            "entitlement_id": self.entitlement_id,
            "request_id": self.request_id,
            "tester_subject_sha256": self.tester_subject_sha256,
            "artifact_id": self.artifact_id,
            "artifact_sha256": self.artifact_sha256,
            "terms_sha256": self.terms_sha256,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "expires_at": self.expires_at,
            "revoked": self.revoked,
            "development_repository_access": False,
        }


class ArtifactEntitlementStore:
    """Deterministic mock of human approval, entitlement, download, and revoke."""

    def __init__(self, *, signing_key: bytes, max_authorization_ttl: int = 900) -> None:
        require(
            len(signing_key) >= 16,
            "GITHUB_APP_ARTIFACT_SIGNING_KEY_INVALID",
            "Artifact authorization requires an in-memory test signing key.",
            status="BLOCKED",
        )
        self._signing_key = bytes(signing_key)
        self.max_authorization_ttl = max_authorization_ttl
        self._requests: dict[str, TesterRequest] = {}
        self._entitlements: dict[str, ArtifactEntitlement] = {}
        self._authorization_replay: dict[str, str] = {}

    def record_request(self, request: TesterRequest) -> dict[str, Any]:
        prior = self._requests.get(request.request_id)
        require(
            prior is None or prior == request,
            "GITHUB_APP_TESTER_REQUEST_REPLAY_CONFLICT",
            "The tester request identity already binds different bytes.",
            status="BLOCKED",
        )
        self._requests[request.request_id] = request
        return _receipt(
            "evidence-lane.github-app-tester-request-receipt.v1",
            status="PASS",
            request_id=request.request_id,
            tester_subject_sha256=request.tester_subject_sha256,
            requested_scope=request.requested_scope,
            requested_at=request.requested_at,
            development_repository_access=False,
            source_write_authorized=False,
        )

    def approve(
        self,
        *,
        entitlement_id: str,
        request_id: str,
        artifact_id: str,
        artifact_sha256: str,
        terms_sha256: str,
        approved_by: str,
        approved_at: str,
        expires_at: str,
    ) -> tuple[ArtifactEntitlement, dict[str, Any]]:
        request = self._requests.get(request_id)
        require(
            request is not None,
            "GITHUB_APP_TESTER_REQUEST_MISSING",
            "Human approval requires the exact recorded tester request.",
            status="BLOCKED",
        )
        request = cast(TesterRequest, request)
        approved = _timestamp(approved_at, field="approved_at")
        expires = _timestamp(expires_at, field="expires_at")
        require(
            expires > approved,
            "GITHUB_APP_ENTITLEMENT_EXPIRY_INVALID",
            "Artifact entitlement must expire after approval.",
            status="BLOCKED",
        )
        artifact_hash = str(artifact_sha256).strip().upper()
        terms_hash = str(terms_sha256).strip().upper()
        require(
            bool(_SHA256.fullmatch(artifact_hash))
            and bool(_SHA256.fullmatch(terms_hash)),
            "GITHUB_APP_ENTITLEMENT_HASH_INVALID",
            "Artifact and terms identities must be exact SHA-256 values.",
            status="BLOCKED",
        )
        entitlement = ArtifactEntitlement(
            entitlement_id=_identifier(entitlement_id, field="entitlement_id"),
            request_id=request.request_id,
            tester_subject_sha256=request.tester_subject_sha256,
            artifact_id=_identifier(artifact_id, field="artifact_id"),
            artifact_sha256=artifact_hash,
            terms_sha256=terms_hash,
            approved_by=_identifier(approved_by, field="approved_by"),
            approved_at=_iso(approved),
            expires_at=_iso(expires),
        )
        prior = self._entitlements.get(entitlement.entitlement_id)
        require(
            prior is None or prior == entitlement,
            "GITHUB_APP_ENTITLEMENT_REPLAY_CONFLICT",
            "The entitlement identity already binds different bytes.",
            status="BLOCKED",
        )
        self._entitlements[entitlement.entitlement_id] = entitlement
        receipt = _receipt(
            "evidence-lane.github-app-entitlement-approval-receipt.v1",
            status="PASS",
            entitlement=entitlement.as_dict(),
            human_approval_recorded=True,
            development_repository_access=False,
            source_write_authorized=False,
            pointer_moved=False,
            hil_inferred=False,
        )
        return entitlement, receipt

    def revoke(
        self, *, entitlement_id: str, revoked_by: str, revoked_at: str
    ) -> dict[str, Any]:
        entitlement = self._entitlements.get(entitlement_id)
        require(
            entitlement is not None,
            "GITHUB_APP_ENTITLEMENT_MISSING",
            "The requested entitlement does not exist.",
            status="BLOCKED",
        )
        entitlement = cast(ArtifactEntitlement, entitlement)
        revoked = replace(entitlement, revoked=True)
        self._entitlements[entitlement_id] = revoked
        return _receipt(
            "evidence-lane.github-app-entitlement-revocation-receipt.v1",
            status="PASS",
            entitlement_id=entitlement_id,
            revoked_by=_identifier(revoked_by, field="revoked_by"),
            revoked_at=_iso(_timestamp(revoked_at, field="revoked_at")),
            artifact_access="DENIED",
        )

    def issue_authorization(
        self,
        *,
        entitlement_id: str,
        authorization_id: str,
        issued_at: str,
        expires_at: str,
    ) -> tuple[str, dict[str, Any]]:
        entitlement = self._entitlements.get(entitlement_id)
        require(
            entitlement is not None,
            "GITHUB_APP_ENTITLEMENT_MISSING",
            "Artifact authorization requires an exact entitlement.",
            status="BLOCKED",
        )
        entitlement = cast(ArtifactEntitlement, entitlement)
        require(
            not entitlement.revoked,
            "GITHUB_APP_ENTITLEMENT_REVOKED",
            "A revoked entitlement cannot authorize artifact access.",
            status="BLOCKED",
        )
        issued = _timestamp(issued_at, field="issued_at")
        expires = _timestamp(expires_at, field="expires_at")
        entitlement_expires = _timestamp(
            entitlement.expires_at, field="entitlement.expires_at"
        )
        require(
            0 < (expires - issued).total_seconds() <= self.max_authorization_ttl
            and expires <= entitlement_expires,
            "GITHUB_APP_ARTIFACT_AUTHORIZATION_TTL_INVALID",
            "The artifact authorization is stale or exceeds its entitlement.",
            status="BLOCKED",
        )
        claims = {
            "schema": "evidence-lane.github-app-artifact-authorization.v1",
            "authorization_id": _identifier(authorization_id, field="authorization_id"),
            "entitlement_id": entitlement.entitlement_id,
            "tester_subject_sha256": entitlement.tester_subject_sha256,
            "artifact_id": entitlement.artifact_id,
            "artifact_sha256": entitlement.artifact_sha256,
            "issued_at": _iso(issued),
            "expires_at": _iso(expires),
        }
        encoded = base64.urlsafe_b64encode(canonical_json_bytes(claims)).rstrip(b"=")
        signature = (
            hmac.new(self._signing_key, encoded, hashlib.sha256)
            .hexdigest()
            .encode("ascii")
        )
        token = (encoded + b"." + signature).decode("ascii")
        receipt = _receipt(
            "evidence-lane.github-app-artifact-authorization-receipt.v1",
            status="PASS",
            claims_sha256=sha256_bytes(canonical_json_bytes(claims)),
            authorization_id=claims["authorization_id"],
            entitlement_id=entitlement.entitlement_id,
            artifact_id=entitlement.artifact_id,
            artifact_sha256=entitlement.artifact_sha256,
            expires_at=claims["expires_at"],
            authorization_value_persisted=False,
            development_repository_access=False,
        )
        return token, receipt

    def authorize_download(
        self,
        *,
        authorization: str,
        artifact_id: str,
        artifact_bytes: bytes,
        now: str,
    ) -> dict[str, Any]:
        try:
            encoded, supplied_signature = authorization.encode("ascii").split(b".", 1)
        except ValueError:
            encoded, supplied_signature = b"", b""
        expected = (
            hmac.new(self._signing_key, encoded, hashlib.sha256)
            .hexdigest()
            .encode("ascii")
        )
        require(
            bool(encoded) and hmac.compare_digest(supplied_signature, expected),
            "GITHUB_APP_ARTIFACT_AUTHORIZATION_INVALID",
            "The artifact authorization signature is invalid.",
            status="BLOCKED",
        )
        padding = b"=" * (-len(encoded) % 4)
        try:
            claims = json.loads(base64.urlsafe_b64decode(encoded + padding))
        except (ValueError, json.JSONDecodeError) as exc:
            require(
                False,
                "GITHUB_APP_ARTIFACT_AUTHORIZATION_INVALID",
                "The signed artifact authorization is malformed.",
                status="BLOCKED",
                error=str(exc),
            )
        require(
            isinstance(claims, Mapping),
            "GITHUB_APP_ARTIFACT_AUTHORIZATION_INVALID",
            "Artifact authorization claims must be an object.",
            status="BLOCKED",
        )
        claims = cast(Mapping[str, Any], claims)
        entitlement = self._entitlements.get(str(claims.get("entitlement_id")))
        require(
            entitlement is not None and not entitlement.revoked,
            "GITHUB_APP_ENTITLEMENT_MISSING_OR_REVOKED",
            "The artifact entitlement is absent or revoked.",
            status="BLOCKED",
        )
        entitlement = cast(ArtifactEntitlement, entitlement)
        exact_now = _timestamp(now, field="now")
        require(
            exact_now <= _timestamp(claims.get("expires_at"), field="expires_at")
            and exact_now
            <= _timestamp(entitlement.expires_at, field="entitlement.expires_at"),
            "GITHUB_APP_ARTIFACT_AUTHORIZATION_EXPIRED",
            "The artifact authorization or entitlement has expired.",
            status="BLOCKED",
        )
        exact_artifact_id = _identifier(artifact_id, field="artifact_id")
        actual_sha = sha256_bytes(artifact_bytes)
        require(
            exact_artifact_id == entitlement.artifact_id
            and claims.get("artifact_sha256") == entitlement.artifact_sha256
            and actual_sha == entitlement.artifact_sha256,
            "GITHUB_APP_ARTIFACT_SUBSTITUTION_BLOCKED",
            "The supplied artifact does not match the exact entitled identity.",
            status="BLOCKED",
            expected_artifact_id=entitlement.artifact_id,
            expected_sha256=entitlement.artifact_sha256,
            actual_sha256=actual_sha,
        )
        authorization_id = str(claims.get("authorization_id"))
        claims_sha = sha256_bytes(canonical_json_bytes(dict(claims)))
        prior = self._authorization_replay.get(authorization_id)
        require(
            prior is None or prior == claims_sha,
            "GITHUB_APP_ARTIFACT_AUTHORIZATION_REPLAY_CONFLICT",
            "The authorization identity was reused with different claims.",
            status="BLOCKED",
        )
        self._authorization_replay[authorization_id] = claims_sha
        return _receipt(
            "evidence-lane.github-app-artifact-download-receipt.v1",
            status="PASS",
            authorization_id=authorization_id,
            entitlement_id=entitlement.entitlement_id,
            artifact_id=entitlement.artifact_id,
            artifact_sha256=actual_sha,
            authorization_value_persisted=False,
            artifact_bytes_persisted=False,
            development_repository_access=False,
            source_write_authorized=False,
        )

    def record_installation(
        self,
        *,
        entitlement_id: str,
        installation_receipt_id: str,
        artifact_id: str,
        artifact_sha256: str,
        host_profile: str,
        result: str,
        recorded_at: str,
    ) -> dict[str, Any]:
        """Seal a caller-reported install result without performing installation."""

        entitlement = self._entitlements.get(entitlement_id)
        require(
            entitlement is not None and not entitlement.revoked,
            "GITHUB_APP_ENTITLEMENT_MISSING_OR_REVOKED",
            "An installation receipt requires an active entitlement.",
            status="BLOCKED",
        )
        entitlement = cast(ArtifactEntitlement, entitlement)
        exact_hash = str(artifact_sha256).strip().upper()
        require(
            _identifier(artifact_id, field="artifact_id") == entitlement.artifact_id
            and exact_hash == entitlement.artifact_sha256,
            "GITHUB_APP_INSTALLATION_ARTIFACT_MISMATCH",
            "The installation receipt does not match the entitled artifact.",
            status="BLOCKED",
        )
        exact_result = result.strip().upper()
        require(
            exact_result in {"VERIFIED", "FAILED", "BLOCKED"},
            "GITHUB_APP_INSTALLATION_RESULT_INVALID",
            "The installation receipt result is unsupported.",
            status="BLOCKED",
        )
        return _receipt(
            "evidence-lane.github-app-tester-installation-receipt.v1",
            status="PASS",
            installation_receipt_id=_identifier(
                installation_receipt_id, field="installation_receipt_id"
            ),
            entitlement_id=entitlement.entitlement_id,
            artifact_id=entitlement.artifact_id,
            artifact_sha256=entitlement.artifact_sha256,
            host_profile=_identifier(host_profile, field="host_profile"),
            installation_result=exact_result,
            recorded_at=_iso(_timestamp(recorded_at, field="recorded_at")),
            installation_performed_by_contract=False,
            development_repository_access=False,
            source_write_authorized=False,
            pointer_moved=False,
            hil_inferred=False,
        )

    def record_feedback(
        self,
        *,
        entitlement_id: str,
        feedback_id: str,
        outcome: str,
        visible_feedback: str,
        recorded_at: str,
    ) -> dict[str, Any]:
        """Record privacy-minimized tester feedback linked to one entitlement."""

        entitlement = self._entitlements.get(entitlement_id)
        require(
            entitlement is not None,
            "GITHUB_APP_ENTITLEMENT_MISSING",
            "Tester feedback requires the exact entitlement identity.",
            status="BLOCKED",
        )
        exact_feedback = visible_feedback.strip()
        require(
            0 < len(exact_feedback.encode("utf-8")) <= 16_384,
            "GITHUB_APP_FEEDBACK_INVALID",
            "Feedback must be non-empty and within the bounded visible payload limit.",
            status="BLOCKED",
        )
        exact_outcome = outcome.strip().upper()
        require(
            exact_outcome in {"PASS", "FAIL", "BLOCKED", "NEEDS_CORRECTION"},
            "GITHUB_APP_FEEDBACK_OUTCOME_INVALID",
            "The tester feedback outcome is unsupported.",
            status="BLOCKED",
        )
        return _receipt(
            "evidence-lane.github-app-tester-feedback-receipt.v1",
            status="PASS",
            feedback_id=_identifier(feedback_id, field="feedback_id"),
            entitlement_id=entitlement_id,
            outcome=exact_outcome,
            visible_feedback_sha256=sha256_bytes(exact_feedback.encode("utf-8")),
            visible_feedback_bytes=len(exact_feedback.encode("utf-8")),
            raw_feedback_persisted=False,
            recorded_at=_iso(_timestamp(recorded_at, field="recorded_at")),
            authority_effect="NONE",
            pointer_moved=False,
            hil_inferred=False,
        )


def receipt_contains_secret(receipt: Mapping[str, Any]) -> bool:
    """Bounded negative helper used by contract tests and SDK adapters."""

    def walk(value: object) -> bool:
        if isinstance(value, Mapping):
            for key, item in cast(Mapping[object, object], value).items():
                if str(key).lower() in _FORBIDDEN_SECRET_KEYS:
                    return True
                if walk(item):
                    return True
        elif isinstance(value, (list, tuple)):
            return any(walk(item) for item in value)
        return False

    return walk(receipt)
