"""Public failures that do not expose exception traces or credentials."""

from __future__ import annotations

from typing import Any


class LaneError(Exception):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def public(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class EvidenceLaneError(LaneError):
    """Preserved base-component error contract, adapted to the v4 transport boundary."""

    def __init__(self, code: str, message: str, status: str = "FAIL",
                 details: dict[str, Any] | None = None):
        super().__init__(code, message, details=details)
        self.status = status

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def as_dict(self) -> dict[str, Any]:
        return {**self.public(), "status": self.status}


def require(condition: bool, code: str, message: str, *, status: str = "FAIL",
            **details: Any) -> None:
    if not condition:
        raise EvidenceLaneError(code, message, status=status, details=details)
