"""Small pure operations used by ingestion and runtime qualification."""

from __future__ import annotations

import hashlib


def hash_text(arguments: dict) -> dict:
    text = arguments["text"]
    if not isinstance(text, str):
        raise TypeError("Text is required")
    content = text.encode("utf-8")
    return {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
