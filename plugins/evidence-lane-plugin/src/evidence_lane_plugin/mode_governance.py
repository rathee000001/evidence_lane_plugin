"""Read and apply direct current Codex ENV/UOP operating contracts.

Work classification selects current lanes and action classes. UOP binds exact
action policy, permission, tools, budgets and verification without a predecessor
translation layer.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import Field

from .errors import LaneError, require
from .flash_authority import SessionFlashAuthority
from .hashing import canonical_json_bytes, sha256_bytes
from .redaction import contains_secret, redact
from .registry import ActionSpec, Contract

ENV_UOP_AUTHORITY_BOUNDARY_SCHEMA = 'evidence-lane.env-uop-authority-boundary.v4'
ENV_UOP_EXTERNAL_SECRET_REFERENCE_SCHEMA = 'evidence-lane.external-host-secret-reference.v1'
ENV_UOP_EXTERNAL_SECRET_RECEIPT_SCHEMA = 'evidence-lane.external-host-secret-receipt.v1'
ENV_UOP_ACTION_POLICY_RECEIPT_SCHEMA = 'evidence-lane.env-uop-action-policy-receipt.v4'
ENV_UOP_EXECUTION_BUDGET_SCHEMA = 'evidence-lane.env-uop-execution-budget.v1'
_ENV_UOP_EXTERNAL_SECRET_TARGET = 'EXTERNAL_HOST_SECRET_PROVIDER'
_ENV_UOP_FORBIDDEN_SECRET_STORES = frozenset({'PROMPT', 'SQLITE', 'CHAT_LINEAGE', 'LINEAGE', 'ASSET', 'TEST'})
_ENV_UOP_SECRET_OUTCOMES = frozenset({'RESOLVED', 'NOT_FOUND', 'DENIED', 'ERROR', 'NOT_REQUIRED'})
_ENV_UOP_SAFE_REFERENCE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}$')
_ENV_UOP_SAFE_ROUTE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
_ENV_UOP_MAX_LANES = 32
_ENV_UOP_MAX_TOOLS = 64
_ENV_UOP_MAX_UNITS_PER_ROUTE = 1024
_ENV_UOP_MAX_TOTAL_UNITS = 8192
_ENV_UOP_SECRET_KEY_RE = re.compile(r'(?i)(?:^|[_-])(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password|private[_-]?key|client[_-]?secret|secret[_-]?value|credential[_-]?value)(?:$|[_-])')

ENV_TABLES = ('env_authority_meta', 'env_work_policy_v4', 'env_project_class_policy_v4',
    'env_source_lane_classification_v4', 'env_workflow_policy_v4', 'env_codex_workflow_stage_v4',
    'env_codex_workflow_edge_v4', 'env_studio_binding_v4', 'env_plan_projection_binding_v4',
    'env_lane_binding_v4', 'env_skill_binding_v4', 'env_action_binding_v4', 'env_tool_registry_v4',
    'env_hook_binding_v4')
UOP_TABLES = ('uop_authority_meta', 'uop_required_gate_v4', 'uop_workflow_gate_v4',
    'uop_permission_policy_v4', 'uop_task_state_policy_v4', 'uop_action_policy_v4',
    'uop_tool_policy_v4', 'uop_fallback_policy_v4', 'uop_verification_policy_v4',
    'uop_plan_projection_policy_v4', 'uop_disclosure_policy_v4', 'uop_source_policy_v4',
    'uop_work_classification_policy_v4', 'uop_project_class_validation_policy_v4',
    'uop_accelerator_policy_v4')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def load_env_uop_runtime_authority(*, flash=None, registry=None):
    """Read typed current contracts from exact verified package bytes."""
    authority = flash or SessionFlashAuthority()
    env = authority.policy_rows('env', ENV_TABLES, registry=registry)
    uop = authority.policy_rows('uop', UOP_TABLES, registry=registry)
    if env['manifest_digest'] != uop['manifest_digest']:
        raise LaneError('ENV_UOP_GENERATION_CHANGED', 'ENV and UOP must belong to one exact locked generation.')
    result = {'schema': 'evidence-lane.env-uop-runtime-authority.v4', 'authority_mode': 'EXACT_BYTES_READ_ONLY',
        'manifest_digest': env['manifest_digest'], 'env_sqlite_sha256': env['database_sha256'],
        'uop_sqlite_sha256': uop['database_sha256'], 'env': env['tables'], 'uop': uop['tables'],
        'work_policies': {row['work_id']: {
            **row, 'lane_templates': json.loads(row['lane_templates_json']),
            'action_classes': json.loads(row['action_classes_json'])}
            for row in env['tables']['env_work_policy_v4']},
        'action_policies': {row['action_name']: row for row in uop['tables']['uop_action_policy_v4']},
        'required_gates': {row['gate_id']: row for row in uop['tables']['uop_required_gate_v4']},
        'project_payload_accessed': False, 'action_effect_executed': False,
        'legacy_translation_layer': False}
    result['runtime_authority_sha256'] = _digest(result)
    return result


def env_uop_authority_boundary(*, runtime=None):
    current = runtime or load_env_uop_runtime_authority()
    body = {'schema': ENV_UOP_AUTHORITY_BOUNDARY_SCHEMA, 'manifest_digest': current['manifest_digest'],
        'env_sqlite_sha256': current['env_sqlite_sha256'], 'uop_sqlite_sha256': current['uop_sqlite_sha256'],
        'ownership': {'source_authority': 'LOCKED_CURRENT_CODEX_ENV_UOP', 'operation_authority': 'CURRENT_TYPED_ACTION_CONTRACT',
            'mutation_authority': 'CURRENT_USER_AUTHORIZATION_AND_OWNING_PROJECT_WRITER', 'project_truth_authority': 'NONE'},
        'action_effect_policy': {'scope': 'CURRENT_TYPED_ACTION_ONLY', 'cross_authority_mutation_allowed': False,
            'classification_authorizes_effect': False},
        'credential_policy': {'source': _ENV_UOP_EXTERNAL_SECRET_TARGET, 'transport': 'REFERENCE_ONLY',
            'value_logging_allowed': False, 'forbidden_storage': sorted(_ENV_UOP_FORBIDDEN_SECRET_STORES)}}
    return {**body, 'boundary_sha256': _digest(body)}


def project_class_contract(project_type, *, selected_lanes=(), runtime=None):
    """Apply the paired current ENV class and UOP validation policy as a proposal."""
    from .lanes import lane_family
    current = runtime or load_env_uop_runtime_authority()
    classes = {'CODE': 'CODE_REPOSITORY', 'DATA': 'DATA_PROJECT', 'DOCUMENT': 'DOCUMENT_PROJECT',
        'MEDIA': 'MEDIA_PROJECT', 'RESEARCH': 'RESEARCH_STUDY', 'MIXED': 'MIXED_PROJECT', 'CUSTOM': 'CUSTOM_PROJECT'}
    key = 'CUSTOM' if project_type.startswith('CUSTOM:') else project_type
    project_class = classes.get(key)
    policy = next((row for row in current['env']['env_project_class_policy_v4'] if row['project_class'] == project_class), None)
    validation = next((row for row in current['uop']['uop_project_class_validation_policy_v4'] if row['project_class'] == project_class), None)
    if policy is None or validation is None or policy['status'] != 'ACTIVE' or validation['status'] != 'ACTIVE':
        raise LaneError('ENV_UOP_PROJECT_CLASS_UNKNOWN', 'The project type requires paired current ENV/UOP policies.')
    known_lanes = {row['lane_id'] for row in current['env']['env_lane_binding_v4']}
    if len(set(selected_lanes)) != len(selected_lanes) or not {lane_family(lane) for lane in selected_lanes} <= known_lanes:
        raise LaneError('ENV_UOP_PROJECT_CLASS_LANE_INVALID', 'Select distinct current owning lanes.')
    policy_lanes = json.loads(policy['lane_ids_json'])
    if not set(policy_lanes) <= known_lanes:
        raise LaneError('ENV_UOP_PROJECT_CLASS_LANE_INVALID', 'The locked project policy references an unavailable lane.')
    body = {'project_class': project_class, 'manifest_digest': current['manifest_digest'],
        'env_policy': policy, 'uop_validation_policy': validation, 'policy_lanes': policy_lanes,
        'selected_lanes': list(selected_lanes), 'selected_lanes_outside_class_defaults': [lane for lane in selected_lanes if lane_family(lane) not in policy_lanes],
        'validation_strategy': policy['ci_strategy'], 'checks_selected_by': 'current_project_and_changed_operations',
        'plugin_maintainer_ci_imposed': False, 'checks_executed': False, 'plan_changed': False}
    return {**body, 'policy_contract_sha256': _digest(body)}


def _mode_policy_projection(mode_id, lanes, current):
    policy_id = 'X' if mode_id.startswith('X:') else mode_id
    policy = current['work_policies'].get(policy_id)
    if policy is None:
        raise LaneError('ENV_UOP_MODE_UNKNOWN', 'The selected mode has no current operating policy.')
    if policy_id != 'X' and not any(lanes == [code if lane == '$CODE_LANE' else lane for lane in policy['lane_templates']]
            for code in ('local_code', 'github_code')):
        raise LaneError('ENV_UOP_MODE_LANE_INVALID', 'The selected mode differs from its locked owning-lane contract.')
    policies = [row for row in current['env']['env_project_class_policy_v4']
        if set(json.loads(row['lane_ids_json'])) & set(lanes)]
    if not policies and policy_id in {'D', 'PL', 'CE', 'RCV', 'X'}:
        policies = list(current['env']['env_project_class_policy_v4'])
    if not policies:
        raise LaneError('ENV_UOP_PROJECT_CLASS_UNKNOWN', 'The mode references no current project class policy.')
    bindings = {row['lane_id']: row for row in current['env']['env_lane_binding_v4']}
    lane_toolchains = []
    for lane in lanes:
        binding = bindings.get(lane)
        if binding is None:
            raise LaneError('ENV_UOP_MODE_LANE_INVALID', 'The selected lane has no current toolchain binding.')
        lane_toolchains.append({'lane_id': lane, 'action_names': json.loads(binding['action_names_json']),
            'action_classes': json.loads(binding['action_classes_json']), 'ordered_tools': json.loads(binding['ordered_tools_json']),
            'binding_sha256': binding['binding_sha256'], 'selection': 'RUN_ONLY_WHEN_ACTIVE_ACTION_REQUIRES_TOOL'})
    return {'work_classification': {'work_id': policy_id, 'work_name': policy['work_name'],
            'action_classes': policy['action_classes'], 'source': 'env_work_policy_v4'},
        'project_class_applicability': {'class_policies': policies,
            'basis': 'current_lane_intersection_not_project_reclassification'},
        'conditional_toolchain': {'authority': 'env_lane_binding_v4', 'lane_bindings': lane_toolchains,
            'all_tools_run_each_turn': False, 'declaration_proves_readiness': False,
            'missing_required_primary_behavior': 'TRY_DECLARED_SAME_CONTRACT_FALLBACK_ELSE_FAIL_CLOSED',
            'cross_class_silent_fallback_allowed': False}}


def bind_env_uop_action_policy(action_name, *, credential_reference=None, runtime=None):
    """Bind one current typed action policy without executing or authorizing it."""
    current = runtime or load_env_uop_runtime_authority()
    if not isinstance(action_name, str) or action_name not in current['action_policies']:
        raise LaneError('ENV_UOP_ACTION_UNKNOWN', 'Select one exact current typed action.')
    policy = current['action_policies'][action_name]
    effects = json.loads(policy['authority_effects_json'])
    body = {'schema': ENV_UOP_ACTION_POLICY_RECEIPT_SCHEMA, 'action_name': action_name,
        'action_policy': policy, 'authority_effects': effects,
        'action_policy_sha256': _digest(policy), 'manifest_digest': current['manifest_digest'],
        'credential_receipt': validate_env_uop_external_secret_reference(credential_reference) if credential_reference is not None else None,
        'effect_executed': False, 'cross_authority_mutation': False, 'downstream_effect_authorized': False}
    return {**body, 'receipt_sha256': _digest(body)}


def govern_mode_selection(selected_modes, *, request, selection_source, runtime=None):
    """Preserve ordered work classifications over current lanes and action classes."""
    current = runtime or load_env_uop_runtime_authority()
    request_digest = hashlib.sha256(request.encode()).hexdigest()
    contracts = []
    known_lanes = {row['lane_id'] for row in current['env']['env_lane_binding_v4']}
    for selected in selected_modes:
        mode_id = str(selected['id'])
        policy_id = 'X' if mode_id.startswith('X:') else mode_id
        policy = current['work_policies'].get(policy_id)
        if policy is None:
            raise LaneError('ENV_UOP_MODE_UNKNOWN', 'The selected mode has no current operating policy.')
        lanes = selected['canonical_lanes']
        if len(set(lanes)) != len(lanes) or not set(lanes) <= known_lanes:
            raise LaneError('ENV_UOP_MODE_LANE_INVALID', 'Mode classification must use the current separate owning lanes.')
        dependency = selected.get('schema', {}).get('dependency_policy') if selected.get('custom') else None
        body = {'schema': 'evidence-lane.mode-governance-contract.v4', 'mode_id': mode_id, 'mode_name': selected['name'],
            'selection_source': selection_source, 'request_sha256': request_digest, 'canonical_lanes': lanes,
            'manifest_digest': current['manifest_digest'], 'scan_order': policy['scan_order'],
            'unit_of_work': policy['unit_of_work'], 'workflow_order': policy['workflow_order'],
            'recursive_loop': policy['recursive_loop'], 'validation_gate': policy['validation_gate'],
            'exit_write_target': policy['exit_write_target'],
            'action_classes': policy['action_classes'],
            'required_gates': sorted(current['required_gates']),
            **_mode_policy_projection(mode_id, lanes, current),
            'project_selected_checks': bool(policy['ci_cd_required']), 'dependency_policy': dependency,
            'effect_executed': False, 'plan_changed': False, 'selection_authorizes_work': False,
            'legacy_translation_layer': False}
        contracts.append({**body, 'contract_sha256': _digest(body)})
    result = {'schema': 'evidence-lane.mode-governance-selection.v4', 'selection_source': selection_source,
        'request_sha256': request_digest, 'contracts': contracts, 'manifest_digest': current['manifest_digest'],
        'selection_authorizes_work': False, 'mode_sector_created': False, 'plan_changed': False}
    return {**result, 'selection_sha256': _digest(result)}


def validate_mode_governance_selection(value):
    body = {key: item for key, item in value.items() if key != 'selection_sha256'}
    runtime = load_env_uop_runtime_authority()
    if (value.get('schema') != 'evidence-lane.mode-governance-selection.v4'
            or _digest(body) != value.get('selection_sha256')
            or value.get('manifest_digest') != runtime['manifest_digest']
            or not 1 <= len(value.get('contracts', [])) <= 16):
        raise LaneError('ENV_UOP_SELECTION_CHANGED', 'The selected mode contract differs from current locked policy.')
    if (any(value.get(field) is not False for field in ('selection_authorizes_work', 'mode_sector_created', 'plan_changed'))
            or len({row['mode_id'] for row in value['contracts']}) != len(value['contracts'])):
        raise LaneError('ENV_UOP_SELECTION_CHANGED', 'Selection cannot authorize work, change Plan or duplicate a mode.')
    for contract in value.get('contracts', []):
        if _digest({k: v for k, v in contract.items() if k != 'contract_sha256'}) != contract.get('contract_sha256'):
            raise LaneError('ENV_UOP_SELECTION_CHANGED', 'An ordered mode contract differs from its digest.')
        mode_id = str(contract.get('mode_id', ''))
        policy = runtime['work_policies'].get('X' if mode_id.startswith('X:') else mode_id)
        if (policy is None or any(contract.get(field) != policy[field] for field in (
                    'scan_order', 'unit_of_work', 'workflow_order', 'recursive_loop', 'validation_gate', 'exit_write_target'))):
            raise LaneError('ENV_UOP_SELECTION_CHANGED', 'The selected mode differs from its actual locked operating policy.')
        projection = _mode_policy_projection(mode_id, contract['canonical_lanes'], runtime)
        if any(contract.get(key) != expected for key, expected in projection.items()):
            raise LaneError('ENV_UOP_SELECTION_CHANGED', 'The mode class and conditional toolchain differ from locked policy.')
        if (contract.get('action_classes') != policy['action_classes']
                or contract.get('required_gates') != sorted(runtime['required_gates'])
                or contract.get('project_selected_checks') != bool(policy['ci_cd_required'])
                or any(contract.get(field) is not False for field in ('effect_executed', 'plan_changed', 'selection_authorizes_work'))
                or contract.get('legacy_translation_layer') is not False
                or any(contract.get(field) != value[field] for field in ('selection_source', 'request_sha256', 'manifest_digest'))):
            raise LaneError('ENV_UOP_SELECTION_CHANGED', 'The selected work policy and attribution differ from locked policy.')
    return value


def validate_task_mode_binding(project, task, *, action=None, registry=None):
    """Intersect the immutable Plan contract with its exact source-selected policy.

    An omitted binding preserves the ordinary explicit task/action contract. It
    does not infer a mode or claim action execution. A supplied binding must
    resolve in this project on every admission/execution/verification boundary.
    """
    reference = task.mode_binding
    if reference is None:
        return None
    from .prompt_index import PromptIndex
    SessionFlashAuthority().verify(registry=registry)
    classified, payload = PromptIndex(project).classification(reference)
    from .capture_routing import capture_text_digest
    mode = classified.get('mode_selection')
    if classified['intent'] not in {'work', 'semantic'} or not isinstance(mode, dict):
        raise LaneError('TASK_MODE_WORK_CLASSIFICATION_REQUIRED', 'Bind a work or semantic classification with explicit modes.')
    selection = mode.get('mode_governance')
    if (not isinstance(selection, dict) or selection.get('selection_sha256') != reference.selection_sha256
            or selection.get('manifest_digest') != reference.manifest_digest
            or selection.get('request_sha256') != capture_text_digest(payload)
            or selection.get('selection_source') != 'PLUGIN_OR_API_EXPLICIT_SELECTION'):
        raise LaneError('TASK_MODE_SELECTION_MISMATCH', 'Use the exact explicitly classified source and locked mode policy.')
    validate_mode_governance_selection(selection)
    routes = [route for contract in selection['contracts'] for route in contract['conditional_toolchain']['lane_bindings']]
    permitted = {name for route in routes for name in route['action_names']}
    if not set(task.allowed_actions) <= permitted or (action is not None and action not in permitted):
        raise LaneError('TASK_MODE_ACTION_SCOPE', 'The task action must belong to an explicitly selected mode lane.')
    # Classification never assigns effects, spends budgets or authorizes work.
    # Real action/tool/worker grants and acceptance checks remain the executor's.
    result = {'schema': 'evidence-lane.task-mode-validation.v4', **reference.model_dump(mode='json'),
        'selected_mode_ids': [row['mode_id'] for row in selection['contracts']],
        'source_actor_id': classified['actor_id'],
        'canonical_lanes': list(dict.fromkeys(lane for row in selection['contracts'] for lane in row['canonical_lanes'])),
        'mode_contract_digests': [row['contract_sha256'] for row in selection['contracts']],
        'action_scope': sorted(task.allowed_actions), 'action_effect_executed': False,
        'mode_selection_authorizes_work': False, 'native_task_attestation': 'not_provided'}
    return {**result, 'binding_validation_digest': _digest(result)}


def route_env_uop_operation(selection, *, mode_id, action_name, lane_id, tool_id,
                            execution_budget, lane_units, tool_invocations, credential_reference=None):
    """Validate one current action/lane/tool route; execution remains engine-owned."""
    validated = validate_mode_governance_selection(selection)
    contract = next((row for row in validated['contracts'] if row['mode_id'] == mode_id), None)
    if contract is None:
        raise LaneError('ENV_UOP_ACTION_ROUTE_INVALID', 'Select an action from this exact work classification.')
    route = next((row for row in contract['conditional_toolchain']['lane_bindings'] if row['lane_id'] == lane_id), None)
    if route is None or action_name not in route['action_names'] or tool_id not in route['ordered_tools']:
        raise LaneError('ENV_UOP_ACTION_ROUTE_INVALID', 'The action and tool must belong to this exact selected work lane.')
    budget = _normalize_env_uop_execution_budget(execution_budget,
        canonical_lanes=list(dict.fromkeys(lane for row in validated['contracts'] for lane in row['canonical_lanes'])))
    requested_lane = _env_uop_budget_units(lane_units, field='lane_units')
    requested_tool = _env_uop_budget_units(tool_invocations, field='tool_invocations')
    if requested_lane > budget['lane_units'][lane_id] or requested_tool > budget['tool_invocations'].get(tool_id, 0):
        raise LaneError('ENV_UOP_ROUTE_BUDGET_EXCEEDED', 'The requested action route exceeds its exact lane/tool allocation.')
    policy = bind_env_uop_action_policy(action_name, credential_reference=credential_reference)
    result = {'schema': 'evidence-lane.env-uop-action-route.v4', 'selection_sha256': selection['selection_sha256'],
        'mode_id': mode_id, 'action_name': action_name, 'lane_id': lane_id, 'tool_id': tool_id,
        'action_policy': policy, 'budget': budget,
        'allocation_checked': {'lane_units': requested_lane, 'tool_invocations': requested_tool},
        'budget_consumed': False, 'effect_executed': False, 'downstream_effect_authorized': False}
    return {**result, 'route_sha256': _digest(result)}


class ModeClassify(Contract):
    request: str = Field(min_length=1, max_length=12000)
    explicit_modes: list[str] = Field(default_factory=list, max_length=16)
    code_lane: Literal['local_code', 'github_code'] = 'local_code'
    custom_modes: list[dict] = Field(default_factory=list, max_length=8)


class ModeClassification(Contract):
    classification: dict
    plan_changed: Literal[False] = False
    effects_authorized: Literal[False] = False


class OperatingPolicyInspect(Contract):
    role: Literal['env', 'uop'] = 'env'
    query: str = Field(min_length=1, max_length=240)
    limit: int = Field(default=8, ge=1, le=20)


class OperatingPolicyResult(Contract):
    role: str
    manifest_digest: str
    matches: list[dict]
    query_mode: Literal['owning_sqlite_fts5_bm25'] = 'owning_sqlite_fts5_bm25'
    project_payload_accessed: Literal[False] = False


def register_mode_actions(engine):
    def classify(context, request):
        from .operating_modes import classify_operating_modes
        if contains_secret(request.model_dump(mode='json')):
            raise LaneError('ENV_UOP_SECRET_VALUE_FORBIDDEN', 'Mode input must not contain raw credentials.')
        result = classify_operating_modes(request.request, explicit_modes=request.explicit_modes,
            code_lane=request.code_lane, custom_modes=request.custom_modes)
        return ModeClassification(classification=redact(result))

    def inspect(context, request):
        result = SessionFlashAuthority().query_policy(request.role, request.query, request.limit, registry=engine.registry)
        return OperatingPolicyResult(role=request.role, manifest_digest=result['manifest_digest'], matches=result['matches'])

    engine.registry.register(ActionSpec('mode_classify', 'Classify ordered work policies, owning lanes and action classes without creating a sector or authorizing work.',
        ModeClassify, ModeClassification, classify, workflow='mode', project_required=False, queryable_in_delta=True,
        required_tools=('ENV_UOP_classifier',)))
    engine.registry.register(ActionSpec('env_uop_inspect', 'Retrieve bounded current ENV/UOP policy from its own locked SQLite/FTS index.',
        OperatingPolicyInspect, OperatingPolicyResult, inspect, workflow='evi', project_required=False, queryable_in_delta=True,
        required_tools=('SQLite_FTS5_BM25', 'rank_bm25')))


# The original secret-reference and exact budget validators are retained below.


def validate_env_uop_external_secret_reference(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a host-owned secret handle and return only a redacted receipt."""

    exact = dict(value)
    require(
        exact.get("schema") == ENV_UOP_EXTERNAL_SECRET_REFERENCE_SCHEMA,
        "ENV_UOP_SECRET_REFERENCE_SCHEMA_INVALID",
        "ENV/UOP credentials require the external host secret-reference schema.",
        status="BLOCKED",
    )
    forbidden_keys = sorted(
        str(key) for key in exact if _ENV_UOP_SECRET_KEY_RE.search(str(key))
    )
    require(
        not forbidden_keys and not contains_secret(exact),
        "ENV_UOP_SECRET_VALUE_FORBIDDEN",
        "Credential values cannot enter ENV/UOP prompts, receipts, or replay state.",
        status="BLOCKED",
        forbidden_keys=forbidden_keys,
    )
    storage_target = str(exact.get("storage_target") or "").strip().upper()
    require(
        storage_target not in _ENV_UOP_FORBIDDEN_SECRET_STORES
        and storage_target == _ENV_UOP_EXTERNAL_SECRET_TARGET,
        "ENV_UOP_SECRET_STORAGE_FORBIDDEN",
        "ENV/UOP credentials may be resolved only by an external host secret provider.",
        status="BLOCKED",
        storage_target=storage_target or None,
        forbidden_storage=sorted(_ENV_UOP_FORBIDDEN_SECRET_STORES),
    )
    provider_id = str(exact.get("provider_id") or "").strip()
    reference_id = str(exact.get("reference_id") or "").strip()
    outcome = str(exact.get("outcome") or "").strip().upper()
    require(
        bool(_ENV_UOP_SAFE_REFERENCE_RE.fullmatch(provider_id))
        and bool(_ENV_UOP_SAFE_REFERENCE_RE.fullmatch(reference_id)),
        "ENV_UOP_SECRET_REFERENCE_INVALID",
        "The external provider and reference identifiers must be bounded opaque handles.",
        status="BLOCKED",
    )
    require(
        outcome in _ENV_UOP_SECRET_OUTCOMES,
        "ENV_UOP_SECRET_OUTCOME_INVALID",
        "The external host secret-provider outcome is not recognized.",
        status="BLOCKED",
        outcome=outcome or None,
    )
    reference_sha256 = sha256_bytes(reference_id.encode("utf-8"))
    redacted_reference = (
        f"{reference_id[:2]}...{reference_id[-2:]}"
        if len(reference_id) > 4
        else "[REDACTED_REFERENCE]"
    )
    core = {
        "schema": ENV_UOP_EXTERNAL_SECRET_RECEIPT_SCHEMA,
        "status": "PASS",
        "provider_id": provider_id,
        "storage_target": _ENV_UOP_EXTERNAL_SECRET_TARGET,
        "reference_redacted": redacted_reference,
        "reference_sha256": reference_sha256,
        "outcome": outcome,
        "value_received": False,
        "value_logged": False,
        "prompt_storage": False,
        "sqlite_storage": False,
        "lineage_storage": False,
        "asset_storage": False,
        "test_storage": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def _env_uop_budget_units(value: Any, *, field: str) -> int:
    require(
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= _ENV_UOP_MAX_UNITS_PER_ROUTE,
        "ENV_UOP_EXECUTION_BUDGET_INVALID",
        "Every ENV/UOP lane and tool budget must be a positive bounded integer.",
        status="BLOCKED",
        field=field,
        max_units=_ENV_UOP_MAX_UNITS_PER_ROUTE,
    )
    return int(value)


def _normalize_env_uop_execution_budget(
    value: Mapping[str, Any],
    *,
    canonical_lanes: list[str],
) -> dict[str, Any]:
    """Validate exact per-lane and per-tool budgets for one selected action route."""

    exact = dict(value)
    require(
        set(exact)
        == {
            "schema",
            "lane_units",
            "tool_invocations",
            "max_total_lane_units",
            "max_total_tool_invocations",
        }
        and exact.get("schema") == ENV_UOP_EXECUTION_BUDGET_SCHEMA,
        "ENV_UOP_EXECUTION_BUDGET_SHAPE_INVALID",
        "An ENV/UOP execution budget requires the exact versioned fields.",
        status="BLOCKED",
    )
    require(
        not contains_secret(exact),
        "ENV_UOP_EXECUTION_BUDGET_SECRET_FORBIDDEN",
        "Secret material cannot enter ENV/UOP execution budgets.",
        status="BLOCKED",
    )
    raw_lanes = exact.get("lane_units")
    raw_tools = exact.get("tool_invocations")
    require(
        isinstance(raw_lanes, Mapping)
        and isinstance(raw_tools, Mapping)
        and bool(raw_tools),
        "ENV_UOP_EXECUTION_BUDGET_MAP_REQUIRED",
        "ENV/UOP execution requires explicit lane and tool budget maps.",
        status="BLOCKED",
    )
    raw_lanes = cast(Mapping[Any, Any], raw_lanes)
    raw_tools = cast(Mapping[Any, Any], raw_tools)
    lanes = [str(item) for item in canonical_lanes]
    require(
        bool(lanes)
        and len(lanes) == len(set(lanes))
        and len(lanes) <= _ENV_UOP_MAX_LANES
        and set(raw_lanes) == set(lanes),
        "ENV_UOP_LANE_BUDGET_MISMATCH",
        "Every selected canonical lane requires exactly one explicit budget.",
        status="BLOCKED",
        required_lanes=lanes,
        supplied_lanes=sorted(str(item) for item in raw_lanes),
    )
    require(
        len(raw_tools) <= _ENV_UOP_MAX_TOOLS,
        "ENV_UOP_TOOL_BUDGET_LIMIT_EXCEEDED",
        "The ENV/UOP tool budget exceeds the bounded tool-route count.",
        status="BLOCKED",
        max_tools=_ENV_UOP_MAX_TOOLS,
    )
    lane_units = {
        lane: _env_uop_budget_units(raw_lanes[lane], field=f"lane_units.{lane}")
        for lane in lanes
    }
    tool_invocations: dict[str, int] = {}
    for raw_tool, raw_units in sorted(raw_tools.items(), key=lambda item: str(item[0])):
        tool_id = str(raw_tool)
        require(
            bool(_ENV_UOP_SAFE_ROUTE_RE.fullmatch(tool_id)),
            "ENV_UOP_TOOL_ROUTE_INVALID",
            "ENV/UOP tool routes require bounded public-safe identifiers.",
            status="BLOCKED",
            tool_id=tool_id,
        )
        tool_invocations[tool_id] = _env_uop_budget_units(
            raw_units,
            field=f"tool_invocations.{tool_id}",
        )
    max_lane = _env_uop_budget_units(
        exact.get("max_total_lane_units"),
        field="max_total_lane_units",
    )
    max_tool = _env_uop_budget_units(
        exact.get("max_total_tool_invocations"),
        field="max_total_tool_invocations",
    )
    lane_total = sum(lane_units.values())
    tool_total = sum(tool_invocations.values())
    require(
        lane_total == max_lane
        and tool_total == max_tool
        and lane_total <= _ENV_UOP_MAX_TOTAL_UNITS
        and tool_total <= _ENV_UOP_MAX_TOTAL_UNITS,
        "ENV_UOP_EXECUTION_BUDGET_TOTAL_MISMATCH",
        "Aggregate ENV/UOP budgets must equal their explicit route allocations.",
        status="BLOCKED",
        lane_total=lane_total,
        max_total_lane_units=max_lane,
        tool_total=tool_total,
        max_total_tool_invocations=max_tool,
        max_total_units=_ENV_UOP_MAX_TOTAL_UNITS,
    )
    core = {
        "schema": ENV_UOP_EXECUTION_BUDGET_SCHEMA,
        "lane_units": lane_units,
        "tool_invocations": tool_invocations,
        "max_total_lane_units": max_lane,
        "max_total_tool_invocations": max_tool,
    }
    return {**core, "budget_sha256": sha256_bytes(canonical_json_bytes(core))}

