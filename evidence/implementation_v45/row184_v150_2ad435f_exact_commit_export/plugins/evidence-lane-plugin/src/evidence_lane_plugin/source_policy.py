"""Fail-closed source-selection and secret-exclusion policy.

The policy is shared by the live worktree index and the optional Git-history
brain.  Included files retain exact bytes; a file is excluded as a whole when
its path or content is unsafe, so an index never stores a silently modified
version of governed source.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import PurePosixPath

_EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".runtime",
        "runtime",
        "dist",
        "build",
        "coverage",
        ".next",
        ".turbo",
        ".cache",
    }
)

_SENSITIVE_NAMES = frozenset(
    {
        ".npmrc",
        ".pypirc",
        ".netrc",
        "credentials",
        "credentials.json",
        "service-account.json",
        "service_account.json",
        "id_rsa",
        "id_ed25519",
    }
)

_SENSITIVE_SUFFIXES = (
    ".pem",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
)

_SECRET_ENV_NAME = re.compile(
    r"(?i)(?:^|_)(?:api_?key|token|secret|password|passwd|private_?key|"
    r"client_?secret|access_?key|credential)(?:$|_)"
)
_TOKEN_PATTERNS = (
    re.compile(r"\bgh[opusr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----[\s\S]*?"
    r"-----END (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"
)
_ASSIGNED_SECRET = re.compile(
    r"(?i)\b(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|passwd)\b\s*[\"']?\s*[:=]\s*"
    r"[\"']?(?P<value>[^\s,;\"'}]{12,})"
)
_PLACEHOLDER_SEGMENTS = frozenset(
    {
        "placeholder",
        "example",
        "dummy",
        "sample",
        "test",
        "testing",
        "fixture",
        "regression",
        "fake",
        "redacted",
        "changeme",
        "replace",
        "your",
        "unset",
        "none",
        "null",
    }
)
_PLACEHOLDER_SEQUENCES = (
    "abcdefghijklmnopqrstuvwxyz",
    "0123456789abcdef",
    "1234567890",
)
_REFERENCE_MARKERS = (
    "os.environ",
    "getenv",
    "process.env",
    "settings.",
    "config.",
    "secret(",
    "${",
    "{{",
)


def normalize_source_path(path: str) -> str:
    """Return one portable path representation for policy checks."""

    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def path_exclusion_reason(path: str) -> str | None:
    """Return a stable code when ``path`` must not enter any source index."""

    normalized = normalize_source_path(path)
    parts = tuple(part.lower() for part in PurePosixPath(normalized).parts)
    if any(part in _EXCLUDED_PARTS for part in parts):
        return "OPERATIONAL_PATH_EXCLUDED"
    if not parts:
        return "INVALID_SOURCE_PATH"
    name = parts[-1]
    if name == ".env" or name.startswith(".env."):
        return "ENV_FILE_EXCLUDED"
    if name in _SENSITIVE_NAMES or name.endswith(_SENSITIVE_SUFFIXES):
        return "CREDENTIAL_FILE_EXCLUDED"
    return None


@lru_cache(maxsize=1)
def known_environment_secrets() -> tuple[bytes, ...]:
    """Return exact configured secret values without exposing their names or bytes."""

    values: set[bytes] = set()
    for name, value in os.environ.items():
        if not _SECRET_ENV_NAME.search(name) or len(value) < 8:
            continue
        encoded = value.encode("utf-8", errors="ignore")
        if encoded:
            values.add(encoded)
    return tuple(sorted(values, key=lambda item: (len(item), item)))


def _is_explicit_placeholder(value: str) -> bool:
    normalized = value.strip().strip("'\"")
    lowered = normalized.lower()
    if not normalized:
        return True
    if any(sequence in lowered for sequence in _PLACEHOLDER_SEQUENCES):
        return True
    segments = set(re.findall(r"[a-z0-9]+", lowered))
    return bool(segments & _PLACEHOLDER_SEGMENTS)


def _assigned_value_is_secret(value: str) -> bool:
    normalized = value.strip().strip("'\"")
    lowered = normalized.lower()
    if _is_explicit_placeholder(normalized):
        return False
    if any(marker in lowered for marker in _REFERENCE_MARKERS):
        return False
    return normalized not in {"password", "passwd", "token", "secret", "api_key"}


def content_exclusion_reason(data: bytes) -> str | None:
    """Return a stable exclusion code without returning or logging secret bytes."""

    if any(secret in data for secret in known_environment_secrets()):
        return "CONFIGURED_SECRET_VALUE_EXCLUDED"
    if b"\x00" in data[:8192]:
        return None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1252")
        except UnicodeDecodeError:
            return None
    if _PRIVATE_KEY.search(text):
        return "PRIVATE_KEY_MATERIAL_EXCLUDED"
    if any(
        not _is_explicit_placeholder(match.group(0))
        for pattern in _TOKEN_PATTERNS
        for match in pattern.finditer(text)
    ):
        return "TOKEN_SHAPED_MATERIAL_EXCLUDED"
    if any(
        _assigned_value_is_secret(match.group("value"))
        for match in _ASSIGNED_SECRET.finditer(text)
    ):
        return "ASSIGNED_SECRET_MATERIAL_EXCLUDED"
    return None


def redact_known_environment_secrets(text: str) -> str:
    """Redact exact configured values from non-source metadata such as Git messages."""

    redacted = text
    for value in known_environment_secrets():
        decoded = value.decode("utf-8", errors="ignore")
        if decoded:
            redacted = redacted.replace(decoded, "[REDACTED]")
    return redacted


__all__ = [
    "content_exclusion_reason",
    "known_environment_secrets",
    "normalize_source_path",
    "path_exclusion_reason",
    "redact_known_environment_secrets",
]
