"""Bounded secret redaction for model-visible results and ChatLineage."""

from __future__ import annotations

import re
from typing import Any

from .secret_patterns import PRIVATE_KEY_PATTERN, TOKEN_PATTERNS

_PATTERNS: tuple[re.Pattern[str], ...] = (
    *TOKEN_PATTERNS,
    re.compile(
        r"(?i)\b(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password)\b\s*[:=]\s*[^\s,;]+"
    ),
    PRIVATE_KEY_PATTERN,
)

_SECRET_KEYS = re.compile(r"(?i)^(authorization|cookie|set_cookie|api_key|apikey|access_token|refresh_token|token|password|secret|client_secret|private_key|credential)$")


def redact_text(value: str) -> str:
    redacted = value
    for pattern in _PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(key): "[REDACTED]" if _SECRET_KEYS.fullmatch(str(key).replace("-", "_")) else redact(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    return value


def contains_secret(value: Any) -> bool:
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _PATTERNS)
    if isinstance(value, dict):
        return any(contains_secret(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_secret(item) for item in value)
    return False
