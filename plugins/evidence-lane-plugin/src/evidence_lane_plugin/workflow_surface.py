"""Public workflow discovery derived from the engine's action registry."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from .plan_runtime import content_digest as digest
from .registry import WORKFLOWS, ActionSpec, Contract


def skill_action_reference(registry, workflow):
    """One package-owned projection shared by generation and installed audits."""
    actions = [item for item in registry.schemas() if item['workflow'] == workflow]
    return {'schema': 'evidence-lane.skill-actions.v4', 'authority': 'engine_typed_action_registry',
        'workflow': workflow, 'action_set_digest': digest(actions),
        'scope': 'primary_workflow_routes; shared context reads are available through the live catalog',
        'native_installation_verified': False,
        'actions': [{**{key: action[key] for key in (
            'name', 'description', 'profile', 'permission', 'mutates', 'project_required',
            'requires_delta', 'queryable_in_delta', 'studio_read', 'cross_project_read')},
            'input_schema_digest': digest(action['inputSchema']),
            'output_schema_digest': digest(action['outputSchema'])} for action in actions]}


class WorkflowQuery(Contract):
    workflow: str | None = Field(default=None, min_length=1, max_length=64,
        json_schema_extra={'enum': [item.name for item in WORKFLOWS] + [None]})


class WorkflowCatalog(Contract):
    schema_version: Literal[4] = 4
    authority: Literal['engine_typed_action_registry'] = 'engine_typed_action_registry'
    workflows: list[dict[str, JsonValue]]
    permission_granted: Literal[False] = False
    native_installation_verified: Literal[False] = False


def register_workflow_actions(engine):
    engine.registry.register(ActionSpec(
        'workflow_catalog', 'Discover current public workflows and their registered actions without granting access.',
        WorkflowQuery, WorkflowCatalog,
        lambda context, request: WorkflowCatalog(workflows=engine.registry.workflow_schemas(request.workflow)),
        project_required=False, workflow='evi',
    ))
