"""ULID-compatible identifiers without a runtime dependency."""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_crockford(value: int, length: int) -> str:
    chars = ["0"] * length
    for index in range(length - 1, -1, -1):
        chars[index] = _ALPHABET[value & 31]
        value >>= 5
    return "".join(chars)


def new_ulid() -> str:
    timestamp_ms = int(time.time_ns() // 1_000_000) & ((1 << 48) - 1)
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode_crockford((timestamp_ms << 80) | randomness, 26)


def prefixed_id(prefix: str) -> str:
    clean = "".join(
        character
        for character in prefix.lower()
        if character.isalnum() or character == "_"
    )
    return f"{clean}_{new_ulid().lower()}"
