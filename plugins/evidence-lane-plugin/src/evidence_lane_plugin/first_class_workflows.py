"""Separate bounded Brain Scaling and project recipe workflows.

Retains the original deterministic slice ordering and recipe proposal behavior.
Recipe and mode are distinct selections; tool execution has its owning actions.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from .errors import LaneError
from .lanes import AUTHORITY_LANE_IDS, SECTOR_LANE_IDS, get_lane, lane_family
from .plan_runtime import content_digest
from .registry import ActionSpec, Contract
from .storage import project_snapshot


class BrainSlice(Contract):
    content_id: str = Field(min_length=1, max_length=192)
    content_sha256: str = Field(pattern=r'^[0-9a-fA-F]{64}$')
    token_count: int = Field(ge=1, le=1000000)
    priority: int = Field(default=0, ge=-1000, le=1000)


class BrainScalingRequest(Contract):
    authority_id: str
    slices: list[BrainSlice] = Field(min_length=1, max_length=200)
    token_budget: int = Field(ge=1, le=1000000)
    max_slices: int = Field(default=20, ge=1, le=200)

    @field_validator('authority_id')
    @classmethod
    def authority(cls, value):
        if value not in AUTHORITY_LANE_IDS:
            raise ValueError('Select one canonical authority')
        return value

    @model_validator(mode='after')
    def distinct_slices(self):
        if len({row.content_id for row in self.slices}) != len(self.slices):
            raise ValueError('Select each evidence slice once')
        return self


class BrainScalingResult(Contract):
    authority_id: str
    selected_slices: list[BrainSlice]
    selected_count: int
    available_count: int
    used_tokens: int
    token_budget: int
    input_digest: str
    token_count_basis: Literal['caller_supplied_estimates'] = 'caller_supplied_estimates'
    content_identity_reverified: Literal[False] = False
    model_training_performed: Literal[False] = False
    authority_merged: Literal[False] = False
    raw_payload_copied: Literal[False] = False


def run_brain_scaling(request: BrainScalingRequest):
    ordered = sorted(request.slices, key=lambda row: (-row.priority, row.content_id, row.content_sha256))
    selected: list[BrainSlice] = []
    used = 0
    for row in ordered:
        if len(selected) >= request.max_slices:
            break
        if used + row.token_count > request.token_budget:
            continue
        selected.append(row)
        used += row.token_count
    return BrainScalingResult(authority_id=request.authority_id, selected_slices=selected,
        selected_count=len(selected), available_count=len(request.slices), used_tokens=used,
        token_budget=request.token_budget, input_digest=content_digest(request.model_dump(mode='json')))


class ProjectRecipeRequest(Contract):
    batch_id: str = Field(min_length=1, max_length=192, pattern=r'^[A-Za-z0-9_.:-]+$')
    requested_outcome: str = Field(min_length=1, max_length=4000)
    explicit_project_type: str | None = Field(default=None, max_length=128,
        pattern=r'^(CODE|DATA|DOCUMENT|MEDIA|RESEARCH|MIXED|CUSTOM:[A-Za-z0-9][A-Za-z0-9 ._/-]{0,119})$')
    explicit_modes: list[str] = Field(default_factory=list, max_length=16)
    custom_modes: list[dict] = Field(default_factory=list, max_length=8)
    mode_request: str | None = Field(default=None, min_length=1, max_length=4000)
    code_lane: Literal['local_code', 'github_code'] = 'local_code'
    git_mode: Literal['AUTO', 'REQUIRED', 'DISABLED'] = 'AUTO'
    max_result_bytes: int = Field(default=524288, ge=8192, le=2097152)


class ProjectRecipeResult(Contract):
    project_id: str
    batch_id: str
    batch_digest: str
    project_type: str
    classification_basis: str
    selected_sector_lanes: list[str]
    authority_references: list[str]
    stage_sequence: list[str]
    artifact_contracts: dict[str, JsonValue]
    project_class_policy: dict
    selected_lane_validation: dict
    project_validation_policy: dict
    mode_classification: dict | None
    repository_context: dict
    execution_workflow: dict
    lane_workflows: list[dict]
    operation_contracts: dict[str, dict]
    workflow_dependency_readiness_checked: Literal[False] = False
    recipe_and_mode_are_distinct: Literal[True] = True
    recipe_digest: str
    proposal_only: Literal[True] = True
    plan_changed: Literal[False] = False
    source_bytes_reverified: Literal[False] = False


_SOURCE_PROJECT_TYPES = {'local_code': 'CODE', 'github_code': 'CODE', 'research': 'RESEARCH',
    'data_excel': 'DATA', 'data': 'DATA', 'tableau': 'DATA', 'power_bi': 'DATA', 'custom': 'DATA',
    'images_ocr': 'MEDIA', 'ppt': 'MEDIA', 'docs': 'DOCUMENT', 'pdf_ocr': 'DOCUMENT', 'artifacts': 'MIXED'}


def _recipe_repository(source_root, mode, *, git_available):
    """Bounded local metadata, never repository enrollment or worktree attestation."""
    from .git_adapter import restoration_git
    root = Path(source_root).resolve(strict=True)
    observation = {'mode': mode, 'state': 'disabled' if mode == 'DISABLED' else 'unavailable',
        'source_root': str(root), 'exact_root_admitted': False, 'head': None, 'branch': None,
        'git_commands_executed': 0, 'dirty_bytes_attested': False, 'remote_contacted': False,
        'observation_atomic': False, 'reason': 'DISABLED_BY_USER' if mode == 'DISABLED' else 'GIT_TOOL_UNAVAILABLE'}
    if mode == 'DISABLED' or not git_available:
        if mode == 'REQUIRED':
            raise LaneError('PROJECT_RECIPE_GIT_REQUIRED', 'The required Git tool is unavailable.')
        return observation
    deadline = time.monotonic() + 10

    def read(arguments):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LaneError('PROJECT_RECIPE_GIT_BUDGET', 'Repository observation exceeded its ten-second budget.')
        observation['git_commands_executed'] += 1
        return restoration_git(root, arguments, check=False, timeout_seconds=remaining, max_stdout_bytes=16384)

    def required(message):
        if mode == 'REQUIRED':
            raise LaneError('PROJECT_RECIPE_GIT_REQUIRED', message, details={'repository_context': observation})
        return observation

    found = read(['rev-parse', '--show-toplevel', '--is-inside-work-tree'])
    if found.returncode != 0:
        observation.update(state='not_detected', reason='GIT_WORKTREE_NOT_DETECTED')
        return required('Git could not verify a worktree at the selected source root.')
    lines = found.stdout.splitlines()
    if len(lines) != 2 or lines[1] != 'true' or not Path(lines[0]).is_absolute():
        raise LaneError('PROJECT_RECIPE_GIT_RESPONSE', 'Git returned an unsupported worktree identity.')
    if Path(lines[0]).resolve(strict=True) != root:
        observation.update(state='parent_not_admitted', reason='PARENT_GIT_WORKTREE_NOT_ADMITTED')
        return required('Select the repository root explicitly before requiring its Git workflow.')
    observation['exact_root_admitted'] = True
    branch = read(['symbolic-ref', '--quiet', '--short', 'HEAD'])
    head = read(['rev-parse', '--verify', 'HEAD^{commit}'])
    if branch.returncode not in (0, 1) or (head.returncode == 0 and not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', head.stdout.strip())):
        raise LaneError('PROJECT_RECIPE_GIT_RESPONSE', 'Git returned an invalid branch or commit identity.')
    branch_name = branch.stdout.strip() if branch.returncode == 0 else None
    if branch_name is not None and (not branch_name or '\n' in branch_name or len(branch_name) > 1024):
        raise LaneError('PROJECT_RECIPE_GIT_RESPONSE', 'Git returned an invalid branch identity.')
    if head.returncode != 0:
        # An unresolved HEAD alone does not prove a new repository: a branch
        # with an existing, broken ref must fail rather than look like an unborn one.
        if branch_name is None or read(['show-ref', '--verify', '--quiet', 'refs/heads/' + branch_name]).returncode != 1:
            raise LaneError('PROJECT_RECIPE_GIT_RESPONSE', 'The repository commit identity is unresolved.')
        observation.update(state='unborn', branch=branch_name, reason='GIT_BRANCH_WITHOUT_COMMIT')
    else:
        observation.update(state='attached' if branch_name else 'detached', head=head.stdout.strip(),
            branch=branch_name, reason='EXACT_SOURCE_ROOT_GIT_METADATA')
    return observation


def _recipe_workflows(registry, lanes, repository):
    """Project the current lane-owned executable contracts once, without readiness probes."""
    from .sector_support import sector_actions
    if registry is None:
        raise LaneError('PROJECT_RECIPE_REGISTRY_REQUIRED', 'A recipe requires the current executable action registry.')
    schemas, views = registry.schemas(), registry.view_schemas()
    contracts, lane_workflows = {}, []

    def contract(name):
        if name not in contracts:
            spec = registry.get(name)
            schema = spec.schema()
            contracts[name] = {key: schema[key] for key in ('permission', 'workflow', 'profile',
                'requires_delta', 'mutates', 'verification_checks', 'toolchain')}
        return name

    for lane in lanes:
        actions = sector_actions(registry, lane, schemas=schemas)
        groups = {'materialization_actions': [], 'read_actions': [], 'change_actions': [], 'refresh_actions': []}
        for action in actions:
            spec = registry.get(action['name'])
            group = ('materialization_actions' if spec.materialization else 'read_actions' if not spec.mutates
                     else 'refresh_actions' if spec.workflow == 'refresh' else 'change_actions')
            groups[group].append(contract(spec.name))
        lane_workflows.append({'lane_id': lane, 'lane_argument': {'lane_id': lane},
            'source_preparation_action': contract('source_prepare_tasks'), **groups,
            'view_contracts': [registry.get_view(lane + '.' + row['view_id'].partition('.')[2]).schema()
                for row in views if row['lane_id'] == lane_family(lane)],
            'selection': 'requested_operations_and_exact_source_route', 'operations_executed': False})
    admitted = repository['exact_root_admitted']
    execution = {'kind': 'git_existing_project' if admitted else 'content_only',
        'plan_actions': [contract('plan_create'), contract('plan_refresh')],
        'execution_actions': [contract('delta_enter_planned'), contract('delta_status')],
        'git_actions': [contract(name) for name in ('git_branch_authority', 'git_sync_selected',
            'source_git_history', 'source_git_impact', 'remote_git_prepare_push', 'remote_git_execute_push')] if admitted else [],
        'git_action_selection': 'explicit_user_scope_and_current_operation_prerequisites',
        'source_preparation_required': True, 'queued_tasks_created': False, 'repository_initialized': False,
        'selected_lanes_changed': False, 'validation': 'current_project_and_changed_operations'}
    return lane_workflows, contracts, execution


def compile_project_recipe(project, request: ProjectRecipeRequest, *, registry=None, git_available=False):
    from .acceptance import ValidationPolicyStore
    from .lanes import lane_artifact_contract
    from .mode_governance import load_env_uop_runtime_authority, project_class_contract
    from .operating_modes import classify_operating_modes
    runtime = load_env_uop_runtime_authority(registry=registry)
    with project_snapshot(project.root), project.lane('sources').connection(read_only=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='intake_batch'").fetchone():
            raise LaneError('SOURCE_BATCH_REQUIRED', 'Register the selected sources before deriving a project recipe.')
        batch = connection.execute('SELECT batch_sha256,source_count FROM intake_batch WHERE batch_id=?', (request.batch_id,)).fetchone()
        if batch is None:
            raise LaneError('SOURCE_BATCH_REQUIRED', 'Select a registered source batch.')
        rows = connection.execute('SELECT lane_id FROM source_occurrence WHERE batch_id=? ORDER BY ordinal LIMIT 257',
                                  (request.batch_id,)).fetchall()
        if len(rows) > 256 or len(rows) != batch['source_count']:
            raise LaneError('SOURCE_BATCH_BUDGET', 'The batch must be complete and within 256 sources.')
        lanes = list(dict.fromkeys(row['lane_id'] for row in rows))
        if not lanes or not {lane_family(lane) for lane in lanes} <= set(SECTOR_LANE_IDS):
            raise LaneError('PROJECT_RECIPE_LANE_SET_INVALID', 'Recipes select retained sectors only.')
        if not {lane_family(lane) for lane in lanes} <= _SOURCE_PROJECT_TYPES.keys():
            raise LaneError('PROJECT_RECIPE_CLASS_REQUIRED', 'The registered lane requires an explicit current project-class mapping.')
        classes = {_SOURCE_PROJECT_TYPES[lane_family(lane)] for lane in lanes}
        project_type = request.explicit_project_type or (next(iter(classes)) if len(classes) == 1 else 'MIXED')
        lane_policies = {lane: project_class_contract(_SOURCE_PROJECT_TYPES[lane_family(lane)], selected_lanes=[lane], runtime=runtime) for lane in lanes}
        if any(value['selected_lanes_outside_class_defaults'] for value in lane_policies.values()):
            raise LaneError('PROJECT_RECIPE_CLASS_MISMATCH', 'The source class mapping differs from current locked lane policy.')
        mode = classify_operating_modes(request.mode_request or request.requested_outcome, explicit_modes=request.explicit_modes or None,
            code_lane=request.code_lane, custom_modes=request.custom_modes) if request.explicit_modes or request.mode_request or request.custom_modes else None
        repository = _recipe_repository(project.source_root, request.git_mode, git_available=git_available)
        workflows, operations, execution = _recipe_workflows(registry, lanes, repository)
        proposal = {'project_id': project.project_id, 'batch_id': request.batch_id, 'batch_digest': batch['batch_sha256'],
            'project_type': project_type, 'classification_basis': 'explicit_user_type' if request.explicit_project_type else 'registered_source_lanes',
            'selected_sector_lanes': lanes, 'authority_references': list(AUTHORITY_LANE_IDS),
            'stage_sequence': ['source_intake', 'recipe_and_plan', 'delta_entry', 'lane_operations', 'verification', 'coordinated_delta_exit'],
            'artifact_contracts': {lane: lane_artifact_contract(get_lane(lane).canonical_lane_id) for lane in lanes},
            'project_class_policy': project_class_contract(project_type, selected_lanes=lanes, runtime=runtime),
            'selected_lane_validation': lane_policies, 'mode_classification': mode,
            'project_validation_policy': ValidationPolicyStore(project).read().model_dump(mode='json'),
            'repository_context': repository, 'execution_workflow': execution,
            'lane_workflows': workflows, 'operation_contracts': operations}
        result = ProjectRecipeResult(**proposal, recipe_digest=content_digest({**proposal, 'requested_outcome': request.requested_outcome}))
        if len(result.model_dump_json().encode('utf-8')) > request.max_result_bytes:
            raise LaneError('PROJECT_RECIPE_RESULT_BUDGET', 'The complete recipe exceeds the selected result budget; increase it or select a smaller source batch.')
        return result


def register_first_class_actions(engine):
    from .tool_routes import ToolRoute
    engine.registry.register(ActionSpec('brain_slice_select', 'Select deterministic metadata slices using explicit estimated-token and item budgets; verify content through its owning authority.',
        BrainScalingRequest, BrainScalingResult, lambda context, request: run_brain_scaling(request),
        workflow='brain-scaling', queryable_in_delta=True))
    def with_git(context, request):
        return compile_project_recipe(engine.directory.open(context.project_id), request,
            registry=engine.registry, git_available=True)

    def content_only(context, request):
        return compile_project_recipe(engine.directory.open(context.project_id), request, registry=engine.registry)

    engine.registry.register(ActionSpec('project_recipe', 'Propose Git or content-only project workflows from a complete registered batch and current lane contracts without changing Plan or sources.',
        ProjectRecipeRequest, ProjectRecipeResult, with_git, workflow='project-recipe', queryable_in_delta=True,
        tool_routes=(ToolRoute('project_recipe.git', with_git, ('Python', 'Git'),
            argument_values=(('git_mode', ('AUTO', 'REQUIRED')),), reason='Bounded read-only observation of the exact source-root repository.'),
            ToolRoute('project_recipe.content', content_only, ('Python',),
                argument_values=(('git_mode', ('AUTO', 'DISABLED')),), reason='Content workflow with Git disabled or unavailable; repository state is not inferred.'))))
