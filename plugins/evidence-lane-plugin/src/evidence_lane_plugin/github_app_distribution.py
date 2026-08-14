"""Provider-neutral, pre-HIL GitHub App and tester-distribution contracts.

The module deliberately stops before registration, credentials, repository
installation, external tester distribution, or publication.  Secrets and
short-lived bearer values exist only in caller-owned memory; receipts contain
hashes and authority-denial facts, never the values themselves.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from .errors import require
from .hashing import canonical_json_bytes, sha256_bytes

GITHUB_APP_MANIFEST_SCHEMA = "evidence-lane.github-app-manifest.v1"
GITHUB_APP_DISTRIBUTION_ABI = "evidence-lane.github-app-distribution.v1"

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SHA256 = re.compile(r"[A-F0-9]{64}")
_SIGNATURE = re.compile(r"sha256=([a-fA-F0-9]{64})")
_PERMISSION_LEVELS = {"read", "write"}
_ALLOWED_REPOSITORY_PERMISSIONS: dict[str, set[str]] = {
    "actions": {"read"},
    "checks": {"read", "write"},
    "contents": {"read"},
    "issues": {"read"},
    "metadata": {"read"},
    "pull_requests": {"read"},
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
        return f"ghs_mock_{digest}"


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
            source_write_authorized=False,
            pointer_moved=False,
            hil_inferred=False,
        )
        self._replay[request.idempotency_key] = (request_sha, token, receipt)
        return token, receipt


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
