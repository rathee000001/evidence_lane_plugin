"""Token verifiers for local testing and standards-based remote hosting."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import Any

from jwt import PyJWKClient, decode
from jwt.exceptions import InvalidTokenError, PyJWKClientError
from mcp.server.auth.provider import AccessToken

_ASYMMETRIC_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA"}
)


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
    algorithms: tuple[str, ...] = ("RS256",)

    def __post_init__(self) -> None:
        if not self.issuer_url.startswith("https://"):
            raise ValueError("OAuth issuer_url must use HTTPS.")
        if not self.jwks_url.startswith("https://"):
            raise ValueError("OAuth jwks_url must use HTTPS.")
        if not self.audience.strip():
            raise ValueError("OAuth audience must be non-empty.")
        if not self.required_scopes:
            raise ValueError("OAuth required_scopes must be non-empty.")
        unsupported = set(self.algorithms) - _ASYMMETRIC_ALGORITHMS
        if unsupported:
            raise ValueError(
                "OAuth algorithms must be asymmetric and allowlisted: "
                + ", ".join(sorted(unsupported))
            )


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
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except (InvalidTokenError, PyJWKClientError, TypeError, ValueError):
            return None
        if not isinstance(claims, dict):
            return None
        scopes = self._scopes(claims)
        if not set(self.config.required_scopes).issubset(scopes):
            return None
        expires_at = claims.get("exp")
        if not isinstance(expires_at, int):
            return None
        subject = str(claims.get("sub", "")).strip()
        if not subject:
            return None
        client_id = str(claims.get("azp") or claims.get("client_id") or subject).strip()
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=expires_at,
            resource=self.config.audience,
            subject=subject,
            claims=claims,
        )
