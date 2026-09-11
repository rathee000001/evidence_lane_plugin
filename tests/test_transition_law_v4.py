"""The public law and actual Plan/session writers share one transition owner."""
# ruff: noqa: F811 - pytest resolves imported fixtures by parameter name
from types import MappingProxyType

import pytest
from evidence_lane_plugin import state_law
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.plan_runtime import PlanStore, content_digest
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.session_authority import SessionAuthority

from tests.test_session_v4 import boot, call, resume_args, selected  # noqa: F401


def test_global_law_describes_actual_owners_and_current_uop_without_project_mutation(selected):
    engine, project, _, client = selected
    before = project.pv_head()
    result = PublicActionSDKDispatcher(engine).execute(ActionRequest(action='lifecycle_transition_law'), client)
    assert result.status == 'ok', result.error
    law = result.result
    assert law['domains'] == state_law.transition_catalog()
    assert set(law['domains']) == {'plan_task', 'project_session'}
    assert law['coverage'] == 'plan_task_transitions_and_project_session_events'
    assert law['independent_state_owners'] == ['plan_revision_replacement', 'delta_runs', 'jobs', 'canon', 'continuation']
    assert not law['execution_authorized'] and not law['native_goal_or_task_attested']
    assert law['law_digest'] == content_digest({key: law[key] for key in ('domains', 'workflow_gates', 'flash_digest')})
    assert {'DELTA_EXIT', 'EXIT_BOOT', 'WORK_HANDOFF_BOUNDARY', 'GOAL_COMPLETION_BOUNDARY'} <= {
        row['event_id'] for row in law['workflow_gates']}
    assert project.pv_head() == before


def test_filtered_catalog_does_not_change_state_or_mutate_its_source(selected):
    engine, project, _, client = selected
    before = project.pv_head()
    response = call(engine, project, client, 'lifecycle_transition_law', {'domain': 'plan_task'})
    assert response.status == 'ok', response.error
    assert set(response.result['domains']) == {'plan_task'}
    returned = state_law.transition_catalog('plan_task')
    returned['plan_task']['additional_checks'].clear()
    returned['plan_task']['events']['task_transition'].clear()
    assert state_law.transition_catalog('plan_task')['plan_task']['additional_checks']
    assert state_law.transition_catalog('plan_task')['plan_task']['events']['task_transition']
    assert call(engine, project, client, 'lifecycle_transition_law', {'domain': 'accepted_pv'}).error.code == 'INVALID_ARGUMENTS'
    assert project.pv_head() == before


def test_plan_writer_uses_the_exposed_law_and_completion_still_requires_evidence(selected, monkeypatch):
    engine, project, _, client = selected
    assert call(engine, project, client, 'plan_create', {'title': 'Law consumer', 'tasks': [
        {'task_id': 'first', 'title': 'First', 'requested_outcome': 'Verify the actual law consumer.'}]}).status == 'ok'
    plan = PlanStore(project)
    before = plan.snapshot().model_dump()
    restricted = {key: value for key, value in state_law.TRANSITION_LAW.items()}
    restricted[('plan_task', state_law.LifecycleEvent.TASK_TRANSITION)] = frozenset()
    with monkeypatch.context() as patch:
        patch.setattr(state_law, 'TRANSITION_LAW', MappingProxyType(restricted))
        with engine.project_work.mutation(project) as lease, pytest.raises(LaneError) as failed:
            plan.transition('first', 'active', lease, expected_revision=1, actor_id=client.client_id)
        assert failed.value.code == 'INVALID_TASK_TRANSITION'
    assert plan.snapshot().model_dump() == before
    with engine.project_work.mutation(project) as lease:
        plan.transition('first', 'active', lease, expected_revision=1, actor_id=client.client_id)
        with pytest.raises(LaneError) as failed:
            plan.transition('first', 'completed', lease, expected_revision=1, actor_id=client.client_id)
        assert failed.value.code == 'DELTA_VERIFICATION_REQUIRED'
    assert plan.snapshot().tasks[0].state == 'active'


def test_session_append_uses_the_same_law_before_event_commit(selected, monkeypatch):
    engine, project, _, client = selected
    opened = boot(engine, project, client)
    assert opened.status == 'ok', opened.error
    authority = SessionAuthority(project)
    before = authority.verify_history()
    restricted = dict(state_law.TRANSITION_LAW)
    restricted[('project_session', state_law.LifecycleEvent.SESSION_RESUME)] = frozenset()
    with monkeypatch.context() as patch:
        patch.setattr(state_law, 'TRANSITION_LAW', MappingProxyType(restricted))
        denied = call(engine, project, client, 'session_resume', resume_args(project, opened.result))
    assert denied.error.code == 'SESSION_TRANSITION_NOT_ALLOWED'
    assert authority.verify_history() == before
    assert call(engine, project, client, 'session_status').result['event_digest'] == opened.result['event_digest']
    resumed = call(engine, project, client, 'session_resume', resume_args(project, opened.result))
    assert resumed.status == 'ok', resumed.error
    assert resumed.result['generation'] == 2


def test_transition_inspector_requires_current_locked_policy(selected, monkeypatch):
    from evidence_lane_plugin.flash_authority import SessionFlashAuthority
    engine, project, _, client = selected

    def unavailable(*args, **kwargs):
        raise LaneError('SESSION_FLASH_MEMBER_HASH_MISMATCH', 'Injected policy mismatch')

    monkeypatch.setattr(SessionFlashAuthority, 'verify', unavailable)
    response = call(engine, project, client, 'lifecycle_transition_law')
    assert response.error.code == 'SESSION_FLASH_MEMBER_HASH_MISMATCH'
