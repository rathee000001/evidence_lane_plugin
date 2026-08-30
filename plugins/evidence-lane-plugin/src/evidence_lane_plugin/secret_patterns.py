"""Shared credential-shape recognizers for intake and visible-output controls."""

from __future__ import annotations

import re

TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgh[opusr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
        r"[A-Za-z0-9_-]{8,}\b"
    ),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
)

PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----[\s\S]*?"
    r"-----END (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"
)


__all__ = ["PRIVATE_KEY_PATTERN", "TOKEN_PATTERNS"]
