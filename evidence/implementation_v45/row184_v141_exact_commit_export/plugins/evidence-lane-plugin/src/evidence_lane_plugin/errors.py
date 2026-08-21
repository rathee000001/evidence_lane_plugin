"""Typed fail-closed errors returned by the engine and MCP layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EvidenceLaneError(Exception):
    code: str
    message: str
    status: str = "FAIL"
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "status": self.status,
            "details": self.details,
        }


def require(
    condition: bool, code: str, message: str, *, status: str = "FAIL", **details: Any
) -> None:
    if not condition:
        raise EvidenceLaneError(
            code=code, message=message, status=status, details=details
        )
