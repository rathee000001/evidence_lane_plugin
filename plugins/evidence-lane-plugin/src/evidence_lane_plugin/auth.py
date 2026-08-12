"""Token verifiers for local testing and standards-based remote hosting."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import Any

from jwt import PyJWKClient, decode
from jwt.exceptions import InvalidTokenError, PyJWKClientError
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

_ASYMMETRIC_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA"}
)
_DEPLOYMENT_ENVIRONMENTS = frozenset({"staging", "production"})

READ_SCOPE = "evidence-lane:read"
WRITE_SCOPE = "evidence-lane:write"
REMOTE_GIT_SCOPE = "evidence-lane:remote-git"
OWNER_ROLE = "owner"


class StaticBearerVerifier:
    """Verify one environment-supplied token for Codex-only private access."""

    def __init__(self, expected_token: str, *, subject: str = "single-user") -> None:
        self._expected_token = expected_token
        self._subject = subject

    async def verify_token(self, token: str) -> AccessToken | None:
        if not secrets.compare_digest(token, self._expected_token):
            return None
        return AccessToken(
            token=token,
            client_id="evidence-lane-single-user",
            scopes=["evidence-lane:read", "evidence-lane:write"],
            subject=self._subject,
        )


@dataclass(frozen=True, slots=True)
class OAuthJWTConfig:
    """Validated resource-server configuration for an established OAuth IdP."""

    issuer_url: str
    jwks_url: str
    audience: str
    required_scopes: tuple[str, ...]
    deployment_environment: str
    allowed_client_ids: tuple[str, ...]
    allowed_roles: tuple[str, ...]
    algorithms: tuple[str, ...] = ("RS256",)
    roles_claim: str = "evidence_lane_roles"
    projects_claim: str = "evidence_lane_projects"
    environment_claim: str = "evidence_lane_environment"
    require_nbf: bool = True
    require_jti: bool = True

    def __post_init__(self) -> None:
        if not self.issuer_url.startswith("https://"):
            raise ValueError("OAuth issuer_url must use HTTPS.")
        if not self.jwks_url.startswith("https://"):
            raise ValueError("OAuth jwks_url must use HTTPS.")
        if not self.audience.startswith("https://"):
            raise ValueError("OAuth audience must be an exact HTTPS MCP resource URL.")
        if not self.required_scopes:
            raise ValueError("OAuth required_scopes must be non-empty.")
        if any(not item.strip() for item in self.required_scopes):
            raise ValueError("OAuth required_scopes cannot contain blank values.")
        if self.deployment_environment not in _DEPLOYMENT_ENVIRONMENTS:
            raise ValueError(
                "OAuth deployment_environment must be staging or production."
            )
        if not self.allowed_client_ids or any(
            not item.strip() for item in self.allowed_client_ids
        ):
            raise ValueError("OAuth allowed_client_ids must contain exact values.")
        if not self.allowed_roles or any(not item.strip() for item in self.allowed_roles):
            raise ValueError("OAuth allowed_roles must contain exact values.")
        for claim_name in (
            self.roles_claim,
            self.projects_claim,
            self.environment_claim,
        ):
            if not claim_name.strip():
                raise ValueError("OAuth policy claim names must be non-empty.")
        unsupported = set(self.algorithms) - _ASYMMETRIC_ALGORITHMS
        if unsupported:
            raise ValueError(
                "OAuth algorithms must be asymmetric and allowlisted: "
                + ", ".join(sorted(unsupported))
            )


def _claim_values(claims: dict[str, Any], claim_name: str) -> frozenset[str]:
    raw = claims.get(claim_name)
    if isinstance(raw, str):
        return frozenset(item for item in raw.split() if item)
    if isinstance(raw, list):
        values: list[str] = []
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                return frozenset()
            values.append(item.strip())
        return frozenset(values)
    return frozenset()


class OAuthJWTVerifier:
    """Verify OAuth access JWTs against a pinned issuer, audience, and JWKS."""

    def __init__(self, config: OAuthJWTConfig) -> None:
        self.config = config
        self._jwk_client = PyJWKClient(config.jwks_url, cache_keys=True)

    @staticmethod
    def _scopes(claims: dict[str, Any]) -> list[str]:
        scopes: set[str] = set()
        raw_scope = claims.get("scope")
        if isinstance(raw_scope, str):
            scopes.update(raw_scope.split())
        elif isinstance(raw_scope, list):
            scopes.update(str(item) for item in raw_scope if str(item).strip())
        permissions = claims.get("permissions")
        if isinstance(permissions, list):
            scopes.update(str(item) for item in permissions if str(item).strip())
        return sorted(scopes)

    async def verify_token(self, token: str) -> AccessToken | None:
        required_claims = [
            "exp",
            "iss",
            "aud",
            "sub",
            self.config.roles_claim,
            self.config.projects_claim,
            self.config.environment_claim,
        ]
        if self.config.require_nbf:
            required_claims.append("nbf")
        if self.config.require_jti:
            required_claims.append("jti")
        try:
            signing_key = await asyncio.to_thread(
                self._jwk_client.get_signing_key_from_jwt,
                token,
            )
            claims = await asyncio.to_thread(
                decode,
                token,
                signing_key.key,
                algorithms=list(self.config.algorithms),
                audience=self.config.audience,
                issuer=self.config.issuer_url,
                options={"require": required_claims},
            )
        except (InvalidTokenError, PyJWKClientError, TypeError, ValueError):
            return None
        if not isinstance(claims, dict):
            return None
        scopes = self._scopes(claims)
        if not set(self.config.required_scopes).issubset(scopes):
            return None
        expires_at = claims.get("exp")
        if not isinstance(expires_at, int) or isinstance(expires_at, bool):
            return None
        if self.config.require_nbf:
            not_before = claims.get("nbf")
            if not isinstance(not_before, int) or isinstance(not_before, bool):
                return None
        if self.config.require_jti:
            token_id = claims.get("jti")
            if not isinstance(token_id, str) or not token_id.strip():
                return None
        subject = str(claims.get("sub", "")).strip()
        if not subject:
            return None
        client_id = str(claims.get("azp") or claims.get("client_id") or "").strip()
        if not client_id or client_id not in set(self.config.allowed_client_ids):
            return None
        environment = claims.get(self.config.environment_claim)
        if environment != self.config.deployment_environment:
            return None
        roles = _claim_values(claims, self.config.roles_claim)
        if not roles or not roles.issubset(set(self.config.allowed_roles)):
            return None
        projects = _claim_values(claims, self.config.projects_claim)
        if not projects:
            return None
        if "*" in projects and OWNER_ROLE not in roles:
            return None
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=expires_at,
            resource=self.config.audience,
            subject=subject,
            claims=claims,
        )


class OAuthAuthorizationError(PermissionError):
    """Fail-closed app authorization error that never includes token material."""

    def __init__(
        self,
        code: str,
        *,
        required_scopes: tuple[str, ...] = (),
    ) -> None:
        super().__init__(code)
        self.code = code
        self.required_scopes = required_scopes


@dataclass(frozen=True, slots=True)
class OAuthToolAuthorizationPolicy:
    """Authorize one verified OAuth principal for one exact MCP tool call."""

    config: OAuthJWTConfig

    def authorize_current_request(
        self,
        *,
        tool_name: str,
        lifecycle: bool,
        project_id: str | None,
    ) -> None:
        access_token = get_access_token()
        if access_token is None:
            raise OAuthAuthorizationError("AUTHENTICATED_OAUTH_CONTEXT_REQUIRED")
        self.authorize_access_token(
            access_token,
            tool_name=tool_name,
            lifecycle=lifecycle,
            project_id=project_id,
        )

    def authorize_access_token(
        self,
        access_token: AccessToken,
        *,
        tool_name: str,
        lifecycle: bool,
        project_id: str | None,
    ) -> None:
        claims = access_token.claims or {}
        if claims.get(self.config.environment_claim) != self.config.deployment_environment:
            raise OAuthAuthorizationError("OAUTH_ENVIRONMENT_MISMATCH")
        if access_token.client_id not in set(self.config.allowed_client_ids):
            raise OAuthAuthorizationError("OAUTH_CLIENT_NOT_ALLOWED")

        roles = _claim_values(claims, self.config.roles_claim)
        if not roles or not roles.issubset(set(self.config.allowed_roles)):
            raise OAuthAuthorizationError("OAUTH_ROLE_NOT_ALLOWED")
        projects = _claim_values(claims, self.config.projects_claim)
        if not projects:
            raise OAuthAuthorizationError("OAUTH_PROJECT_GRANT_REQUIRED")

        required_scopes = {READ_SCOPE}
        if lifecycle:
            required_scopes.add(WRITE_SCOPE)
        if tool_name in {"remote_git_prepare_push", "remote_git_execute_push"}:
            required_scopes.add(REMOTE_GIT_SCOPE)
            if OWNER_ROLE not in roles:
                raise OAuthAuthorizationError("OWNER_ROLE_REQUIRED_FOR_REMOTE_GIT")
        missing_scopes = required_scopes - set(access_token.scopes)
        if missing_scopes:
            raise OAuthAuthorizationError(
                "OAUTH_SCOPE_REQUIRED:" + ",".join(sorted(missing_scopes)),
                required_scopes=tuple(sorted(required_scopes)),
            )

        if (
            lifecycle
            and self.config.deployment_environment == "production"
            and OWNER_ROLE not in roles
        ):
            raise OAuthAuthorizationError("OWNER_ROLE_REQUIRED_IN_PRODUCTION")

        if project_id is not None:
            exact_project = project_id.strip()
            if not exact_project:
                raise OAuthAuthorizationError("EXACT_PROJECT_ID_REQUIRED")
            owner_wildcard = OWNER_ROLE in roles and "*" in projects
            if exact_project not in projects and not owner_wildcard:
                raise OAuthAuthorizationError("OAUTH_PROJECT_NOT_AUTHORIZED")
