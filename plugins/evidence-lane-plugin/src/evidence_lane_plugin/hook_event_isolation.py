"""One-process isolation and state transitions for a native Hook occurrence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .errors import LaneError

HookStage = Literal[
    "created",
    "admitted",
    "classified",
    "handled",
    "delivered",
    "sealed",
    "projected",
    "failed",
]

_NEXT = {
    "created": "admitted",
    "admitted": "classified",
    "classified": "handled",
    "handled": "delivered",
    "delivered": "sealed",
    "sealed": "projected",
}


@dataclass
class HookExecution:
    """Track one event pipeline without persisting raw host input."""

    expected_event: str
    stage: HookStage = "created"
    visited: list[str] = field(default_factory=list)

    def advance(self, stage: HookStage) -> None:
        if self.stage == "failed" or _NEXT.get(self.stage) != stage:
            raise LaneError(
                "HOOK_STAGE_ORDER",
                "The native Hook occurrence attempted an out-of-order stage.",
            )
        self.stage = stage
        self.visited.append(stage)

    def fail(self) -> None:
        self.stage = "failed"

    def receipt(self) -> dict[str, object]:
        return {
            "schema": "evidence-lane.hook-execution-isolation.v4",
            "event": self.expected_event,
            "stage": self.stage,
            "visited": list(self.visited),
            "raw_input_persisted": False,
            "automatic_retry": False,
            "lifecycle_control": False,
        }


__all__ = ["HookExecution", "HookStage"]
