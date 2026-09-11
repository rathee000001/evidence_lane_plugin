"""Executable Plan-task and project-session transition law in the original owner.

Plan, session, job and task exchange authority state remain separate. The table below governs
only its named Plan/session consumers; it does not confer execution authority.
"""
from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from pydantic import Field, JsonValue

from .errors import LaneError
from .registry import ActionSpec, Contract


class LifecycleEvent(StrEnum):
    TASK_TRANSITION = 'task_transition'
    SESSION_BOOT = 'session_boot'
    SESSION_RESUME = 'session_resume'
    SESSION_EXIT = 'session_exit'
    SESSION_RECOVERY_CLOSED = 'session_recovery_closed'


def _pairs(sources, targets):
    return frozenset((source, target) for source in sources for target in targets)


TRANSITION_LAW = MappingProxyType({
    ('plan_task', LifecycleEvent.TASK_TRANSITION): (
        _pairs({'queued'}, {'active', 'cancelled'})
        | _pairs({'active'}, {'completed', 'blocked', 'failed', 'cancelled'})
        | _pairs({'blocked'}, {'queued', 'cancelled'})),
    ('project_session', LifecycleEvent.SESSION_BOOT): _pairs({None, 'closed'}, {'active'}),
    ('project_session', LifecycleEvent.SESSION_RESUME): _pairs({'active'}, {'active'}),
    ('project_session', LifecycleEvent.SESSION_EXIT): _pairs({'active'}, {'closed'}),
    # Existing offline restore and source-binding recovery close the saved
    # session after their own writer/backup checks. This is an internal event,
    # not a public bypass of authenticated session entry or exit.
    ('project_session', LifecycleEvent.SESSION_RECOVERY_CLOSED): _pairs({'active'}, {'closed'}),
})

DOMAIN_CONSUMERS = MappingProxyType({
    'plan_task': {
        'owner': 'plan_runtime.PlanStore.transition',
        'storage_owner': 'plan',
        'additional_checks': ('current_plan_revision', 'exact_task_contract', 'one_active_task',
                              'completed_dependencies', 'restoration_and_recovery_ready',
                              'verified_delta_receipt_before_completion'),
    },
    'project_session': {
        'owner': 'session_authority.SessionAuthority.append',
        'storage_owner': 'receipts',
        'additional_checks': ('locked_flash', 'current_session_head_and_generation',
                              'authenticated_client_ownership', 'safe_work_boundary',
                              'exact_root_pv_on_entry', 'current_runtime_package'),
    },
})


def transition(current, event, target, *, domain):
    """Reject undeclared changes before the owning authority writes an event."""
    allowed = TRANSITION_LAW.get((domain, event))
    if allowed is None or (current, target) not in allowed:
        code = 'INVALID_TASK_TRANSITION' if domain == 'plan_task' else 'SESSION_TRANSITION_NOT_ALLOWED'
        raise LaneError(code, 'The requested state change is not present in its owning transition law.',
            details={'domain': domain, 'event': str(event), 'current': current, 'target': target})
    return target


def transition_catalog(domain=None):
    """Read the same immutable table that Plan and session writes execute."""
    if domain is not None and domain not in DOMAIN_CONSUMERS:
        raise LaneError('TRANSITION_DOMAIN_UNKNOWN', 'Select the Plan-task or project-session domain.')
    selected = [domain] if domain else list(DOMAIN_CONSUMERS)
    return {name: {
        **DOMAIN_CONSUMERS[name],
        'additional_checks': list(DOMAIN_CONSUMERS[name]['additional_checks']),
        'events': {str(event): [{'from': source, 'to': target}
            for source, target in sorted(pairs, key=lambda pair: (pair[0] or '', pair[1]))]
            for (owner, event), pairs in TRANSITION_LAW.items() if owner == name},
    } for name in selected}


class TransitionLawQuery(Contract):
    domain: Literal['plan_task', 'project_session'] | None = None


class TransitionLawResult(Contract):
    contract: Literal['evidence-lane.lifecycle-transition-law.v4'] = 'evidence-lane.lifecycle-transition-law.v4'
    domains: dict[str, JsonValue]
    flash_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    workflow_gates: list[dict[str, JsonValue]]
    law_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    coverage: Literal['plan_task_transitions_and_project_session_events'] = 'plan_task_transitions_and_project_session_events'
    independent_state_owners: list[str]
    project_mutated: Literal[False] = False
    execution_authorized: Literal[False] = False
    native_goal_or_task_attested: Literal[False] = False


def register_transition_law(engine):
    def read(context, request):
        from .flash_authority import SessionFlashAuthority
        from .plan_runtime import content_digest
        policy = SessionFlashAuthority().policy_rows('uop', ('uop_workflow_gate_v4',), registry=engine.registry)
        domains = transition_catalog(request.domain)
        gates = policy['tables']['uop_workflow_gate_v4']
        return TransitionLawResult(domains=domains, flash_digest=policy['manifest_digest'], workflow_gates=gates,
            law_digest=content_digest({'domains': domains, 'workflow_gates': gates, 'flash_digest': policy['manifest_digest']}),
            independent_state_owners=['plan_revision_replacement', 'delta_runs', 'jobs', 'canon', 'continuation'])
    engine.registry.register(ActionSpec('lifecycle_transition_law',
        'Inspect executable Plan/session transitions and current UOP workflow gates without changing state.',
        TransitionLawQuery, TransitionLawResult, read, project_required=False, workflow='evidence-lane'))
