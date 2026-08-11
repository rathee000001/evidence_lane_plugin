"""Single executable transition law for every governed session state change."""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Any

from .errors import require
from .models import SessionState


class LifecycleEvent(StrEnum):
    FLASH_VERIFIED = "FLASH_VERIFIED"
    BUILD_INITIAL = "BUILD_INITIAL"
    CLASSIFY_TASK = "CLASSIFY_TASK"
    RECONCILE_COMPLETED_TASK = "RECONCILE_COMPLETED_TASK"
    BEGIN_EXIT = "BEGIN_EXIT"
    RECOVER_INTERRUPTED_EXIT = "RECOVER_INTERRUPTED_EXIT"
    SEAL_EXIT = "SEAL_EXIT"
    SEAL_INITIAL_RETRY = "SEAL_INITIAL_RETRY"
    HIL_APPROVE = "HIL_APPROVE"
    HIL_APPROVE_WITH_DELTA = "HIL_APPROVE_WITH_DELTA"
    HIL_MORE_RESEARCH = "HIL_MORE_RESEARCH"
    HIL_ROLLBACK = "HIL_ROLLBACK"
    HIL_REJECT = "HIL_REJECT"
    HIL_FAIL = "HIL_FAIL"
    RETURN_TO_ACCEPTED = "RETURN_TO_ACCEPTED"
    BEGIN_NEXT_TURN = "BEGIN_NEXT_TURN"


def _pairs(
    sources: set[SessionState],
    targets: set[SessionState],
) -> frozenset[tuple[SessionState, SessionState]]:
    return frozenset((source, target) for source in sources for target in targets)


_CANDIDATE = {SessionState.PV1_CANDIDATE, SessionState.PVN1_CANDIDATE}
_ACCEPTED = {SessionState.PVN_ACCEPTED, SessionState.PVN1_ACCEPTED}
_IDLE_ENTRY = {
    SessionState.BOOTED,
    SessionState.PVN_ACCEPTED,
    SessionState.PVN1_ACCEPTED,
    SessionState.PVN1_ENTRY,
}
_CLASSIFIABLE = _IDLE_ENTRY | {
    SessionState.CORRECTION_TASK_PENDING,
    SessionState.RESEARCH_TASK_PENDING,
}

TRANSITION_LAW = MappingProxyType(
    {
        LifecycleEvent.FLASH_VERIFIED: frozenset(
            {(SessionState.SESSION_BOOT_FLASH, SessionState.BOOTED)}
        ),
        LifecycleEvent.BUILD_INITIAL: frozenset(
            {(SessionState.BOOTED, SessionState.PV1_CANDIDATE)}
        ),
        LifecycleEvent.CLASSIFY_TASK: _pairs(
            _CLASSIFIABLE,
            {
                SessionState.TASK_CLASSIFIED,
                SessionState.AWAITING_USER_APPLY_COMMIT,
            },
        ),
        LifecycleEvent.RECONCILE_COMPLETED_TASK: _pairs(
            {SessionState.TASK_CLASSIFIED},
            _ACCEPTED,
        ),
        LifecycleEvent.BEGIN_EXIT: _pairs(
            {
                SessionState.TASK_CLASSIFIED,
                SessionState.AWAITING_USER_APPLY_COMMIT,
            },
            {SessionState.EXIT_BUILDING},
        ),
        LifecycleEvent.RECOVER_INTERRUPTED_EXIT: frozenset(
            {(SessionState.EXIT_BUILDING, SessionState.EXIT_BUILDING)}
        ),
        LifecycleEvent.SEAL_EXIT: frozenset(
            {(SessionState.EXIT_BUILDING, SessionState.PVN1_CANDIDATE)}
        ),
        LifecycleEvent.SEAL_INITIAL_RETRY: frozenset(
            {(SessionState.EXIT_BUILDING, SessionState.PV1_CANDIDATE)}
        ),
        LifecycleEvent.HIL_APPROVE: _pairs(_CANDIDATE, _ACCEPTED),
        LifecycleEvent.HIL_APPROVE_WITH_DELTA: _pairs(
            _CANDIDATE, {SessionState.CORRECTION_TASK_PENDING}
        ),
        LifecycleEvent.HIL_MORE_RESEARCH: _pairs(
            _CANDIDATE, {SessionState.RESEARCH_TASK_PENDING}
        ),
        LifecycleEvent.HIL_ROLLBACK: _pairs(_CANDIDATE | _IDLE_ENTRY, _ACCEPTED),
        LifecycleEvent.HIL_REJECT: _pairs(_CANDIDATE, {SessionState.REJECTED_RUN}),
        LifecycleEvent.HIL_FAIL: _pairs(_CANDIDATE, {SessionState.FAILED_RUN}),
        LifecycleEvent.RETURN_TO_ACCEPTED: _pairs(
            {SessionState.REJECTED_RUN, SessionState.FAILED_RUN}, _ACCEPTED
        ),
        LifecycleEvent.BEGIN_NEXT_TURN: _pairs(_ACCEPTED, {SessionState.PVN1_ENTRY}),
    }
)


def transition(
    current: SessionState,
    event: LifecycleEvent,
    target: SessionState,
) -> SessionState:
    allowed = TRANSITION_LAW[event]
    require(
        (current, target) in allowed,
        "SESSION_TRANSITION_NOT_ALLOWED",
        "The requested lifecycle transition is not present in the canonical law.",
        status="BLOCKED",
        current=current.value,
        event=event.value,
        target=target.value,
        allowed=[
            {"from": source.value, "to": destination.value}
            for source, destination in sorted(
                allowed, key=lambda pair: (pair[0].value, pair[1].value)
            )
        ],
    )
    return target


def transition_catalog() -> dict[str, Any]:
    return {
        "schema": "evidence-lane.session-transition-law.v1",
        "single_authority": True,
        "events": {
            event.value: [
                {"from": source.value, "to": target.value}
                for source, target in sorted(
                    pairs, key=lambda pair: (pair[0].value, pair[1].value)
                )
            ]
            for event, pairs in TRANSITION_LAW.items()
        },
    }
