"""Read and verify packaged SDK contracts without executing their owners."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SDK_ROOT = Path(__file__).resolve().parent
MAX_CONTRACT_BYTES = 16_777_216


def _contract_path(relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise ValueError("Select one non-empty SDK-relative contract path.")
    selected = Path(relative)
    if selected.is_absolute() or ".." in selected.parts:
        raise ValueError("SDK contracts must stay under the packaged SDK root.")
    path = (SDK_ROOT / selected).resolve(strict=True)
    try:
        path.relative_to(SDK_ROOT.resolve(strict=True))
    except ValueError:
        raise ValueError("The SDK contract escaped its packaged root.") from None
    cursor = path
    while cursor != SDK_ROOT.parent:
        if cursor.is_symlink():
            raise ValueError("SDK contract links are not admitted.")
        if cursor == SDK_ROOT:
            break
        cursor = cursor.parent
    if not path.is_file() or path.stat().st_size > MAX_CONTRACT_BYTES:
        raise ValueError("The SDK contract is missing or exceeds its read budget.")
    return path


def load_sdk_contract(relative: str, *, expected_schema: str | None = None) -> dict[str, Any]:
    """Return one fresh JSON object from a bounded SDK-relative path."""

    path = _contract_path(relative)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("The SDK contract is not valid UTF-8 JSON.") from None
    if not isinstance(value, dict):
        raise TypeError("The SDK contract must contain one JSON object.")
    if expected_schema is not None and value.get("schema") != expected_schema:
        raise ValueError("The SDK contract schema differs from its selected family.")
    return value


def load_sdk_text(relative: str) -> str:
    """Return one bounded UTF-8 MMD/DOT/Markdown SDK member."""

    try:
        return _contract_path(relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ValueError("The SDK text member is not valid UTF-8.") from None


def verify_sdk_reference(reference: dict[str, Any]) -> dict[str, Any]:
    """Verify one generated path/size/SHA-256 reference and return a copy."""

    if (
        not isinstance(reference, dict)
        or set(reference) not in ({"path", "sha256"}, {"path", "bytes", "sha256"})
    ):
        raise ValueError("Select one exact SDK member reference.")
    path = _contract_path(str(reference["path"]).removeprefix("sdk/"))
    content = path.read_bytes()
    if (
        ("bytes" in reference and len(content) != reference["bytes"])
        or hashlib.sha256(content).hexdigest() != reference["sha256"]
    ):
        raise ValueError("The packaged SDK member differs from its reference.")
    return dict(reference)


def sdk_surface() -> dict[str, Any]:
    """Read the complete generated SDK family registry."""

    return load_sdk_contract(
        "sdk-surface-registry.v4.json",
        expected_schema="evidence-lane.sdk-surface-registry.v4",
    )


__all__ = [
    "load_sdk_contract",
    "load_sdk_text",
    "sdk_surface",
    "verify_sdk_reference",
]
