"""Single-user bearer verifier for the deferred-hosting HTTP boundary."""

from __future__ import annotations

import secrets

from mcp.server.auth.provider import AccessToken


class StaticBearerVerifier:
    """Verify one environment-supplied token without persisting it."""

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
