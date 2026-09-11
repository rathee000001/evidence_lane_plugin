"""Full ENV selection and UOP admission at the real engine tool boundary."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar

from .errors import LaneError
from .flash_authority import SessionFlashAuthority

ENV_UOP_TOOL_ROUTING_SCHEMA = 'evidence-lane.env-uop-tool-routing.v4'


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


class EnvUopRuntime:
    """Verified immutable policy constrains current registered handlers.

    Authentication, writer fencing, source verification and device measurement
    remain in their existing owners; this module binds their exact contracts
    and rechecks those available at this admission boundary.
    """
    def __init__(self, registry, *, flash=None):
        self.registry = registry
        self.flash = flash or SessionFlashAuthority()
        self._snapshot = None
        self._read_batch = ContextVar('evidence_env_uop_read_batch', default=None)

    @contextmanager
    def readonly_batch(self, project_id):
        """Amortize full frozen-registry projection within one bounded read.

        Every child still verifies all packaged Flash bytes and its own exact
        action schema, permissions and pipeline. Full registry verification
        brackets the operation; no data, grants or query results are cached.
        """
        if not self.registry._frozen:
            yield
            return
        prior = self._read_batch.get()
        if prior is not None and prior['project_id'] == project_id:
            yield
            return
        status = self.flash.verify(registry=self.registry)
        token = self._read_batch.set({'project_id': project_id, 'manifest_digest': status.manifest_digest,
            'action_set_digest': status.action_set_digest})
        try:
            yield
            observed = self.flash.verify(registry=self.registry)
            if (observed.manifest_digest != status.manifest_digest
                    or observed.action_set_digest != status.action_set_digest):
                raise LaneError('ENV_UOP_GENERATION_CHANGED', 'The locked operating policy changed during the read.')
        finally:
            self._read_batch.reset(token)

    def _load(self, *, batch=None):
        status = self.flash.verify(registry=None if batch is not None else self.registry)
        if batch is not None and (status.manifest_digest != batch['manifest_digest']
                or status.action_set_digest != batch['action_set_digest']):
            raise LaneError('ENV_UOP_GENERATION_CHANGED', 'The locked operating policy changed during the read.')
        if self._snapshot is not None and self._snapshot['manifest_digest'] == status.manifest_digest:
            return self._snapshot
        env = self.flash.policy_rows('env', ('env_action_binding_v4','env_operation_pipeline_v4',
            'env_lane_binding_v4','env_codex_workflow_stage_v4'),registry=self.registry,max_rows=5000)
        uop = self.flash.policy_rows('uop', ('uop_action_policy_v4','uop_fallback_policy_v4',
            'uop_required_gate_v4','uop_workflow_gate_v4'),registry=self.registry,max_rows=5000)
        if env['manifest_digest'] != uop['manifest_digest']:
            raise LaneError('ENV_UOP_GENERATION_CHANGED','ENV and UOP must belong to one locked generation.')
        self._snapshot = {'manifest_digest':status.manifest_digest,
            'actions':{row['action_name']:row for row in env['tables']['env_action_binding_v4']},
            'pipelines':{(row['action_name'],row['route_id']):json.loads(row['pipeline_json']) for row in env['tables']['env_operation_pipeline_v4']},
            'effects':{row['action_name']:row for row in uop['tables']['uop_action_policy_v4']},
            'fallbacks':{row['workflow_class']:row for row in uop['tables']['uop_fallback_policy_v4']},
            'gates':{row['gate_id']:row for row in uop['tables']['uop_required_gate_v4']},
            'events':{row['event_id']:row for row in uop['tables']['uop_workflow_gate_v4']}}
        return self._snapshot

    def admit(self,spec,context,arguments,selection):
        batch = self._read_batch.get()
        if (batch is not None and (batch['project_id'] != context.project_id
                or spec.mutates or spec.requires_delta or spec.queued
                or spec.permission != 'read' or not spec.queryable_in_delta)):
            batch = None
        policy = self._load(batch=batch)
        binding = policy['actions'].get(spec.name)
        effects = policy['effects'].get(spec.name)
        schema = spec.schema()
        if binding is None or effects is None or binding['schema_sha256'] != _digest(schema):
            raise LaneError('ENV_UOP_ACTION_BINDING_CHANGED','This exact action differs from its locked ENV/UOP binding.')
        declared = json.loads(effects['authority_effects_json'])
        if any(schema[key] != value for key,value in declared.items()):
            raise LaneError('ENV_UOP_EFFECT_BINDING_CHANGED','The action effect contract differs from locked UOP.')
        required = {'USER_AUTHORITY','CONNECTION_SCOPE','PROJECT_BINDING','PERMISSION','PLAN_TASK',
                    'SOURCE_CURRENTNESS','TOOL_READINESS','SAME_CONTRACT_FALLBACK',
                    'EFFECT_RECONCILIATION','VERIFICATION','HOST_PLAN_PROJECTION','DISCLOSURE'}
        if not required <= policy['gates'].keys() or binding['entry_event'] not in policy['events']:
            raise LaneError('ENV_UOP_REQUIRED_GATE_MISSING','The retained operating gates are incomplete.')
        if context.allowed_actions is not None and spec.name not in context.allowed_actions:
            raise LaneError('ACTION_SCOPE_DENIED','The connection does not grant this action.')
        if spec.permission not in context.permissions:
            raise LaneError('PERMISSION_DENIED','This connection lacks the operation permission.')
        if spec.project_required and context.project_id is None:
            raise LaneError('PROJECT_REQUIRED','The selected operation requires its owning project.')
        if spec.requires_delta and context.execution is None:
            raise LaneError('DELTA_REQUIRED','The selected operation requires its current bounded task.')
        if context.authorize is not None:
            context.authorize(spec.permission)
        guard = context.execution.guard if context.execution is not None else None
        if guard is not None:
            guard.check()
        route = policy['pipelines'].get((spec.name,selection['selected_route']))
        if route is None or route.get('fidelity') != 'exact_contract':
            raise LaneError('ENV_UOP_PIPELINE_CHANGED','The selected pipeline is absent or changes operation fidelity.')
        classes = json.loads(binding['workflow_classes_json'])
        for workflow_class in classes:
            fallback = policy['fallbacks'].get(workflow_class)
            if fallback is None or fallback['cross_class_fallback_allowed'] or fallback['silent_fallback_allowed']:
                raise LaneError('ENV_UOP_FALLBACK_POLICY_INVALID','Fallback must stay explicit and within the declared operation class.')
        attempt = next((row for row in selection['attempts'] if row['route_id']==selection['selected_route']),None)
        if attempt is None or not attempt['ready'] or not all(row.get('ready') is True for row in attempt['observations']):
            raise LaneError('ENV_UOP_PIPELINE_NOT_READY','The chosen pipeline lacks current readiness evidence.')
        task = guard.task if guard is not None else None
        source = guard.source_route if guard is not None else None
        selection_context = {**selection['context'], 'entry_event': binding['entry_event'],
            'workflow_classes': classes,
            'task_id': task.task_id if task is not None else None,
            'plan_revision': context.execution.plan_revision if guard is not None else context.expected_revision,
            'requested_outcome_sha256': _digest(task.requested_outcome) if task is not None else None,
            'intent_basis': 'current_plan_task' if task is not None else 'authorized_typed_operation',
            'source_scope': {'route_id': source.selection.route_id,
                'occurrence_ordinals': source.selection.occurrence_ordinals, 'lane_id': source.lane_id,
                'data_locality': 'verified_local_source_scope'} if source is not None else None,
            'unbound_source_locality': 'owning_operation_verifies_inputs' if source is None else None,
            'project_class_selection': 'separate_source_recipe_and_optional_task_mode',
            'project_reclassification_inferred': False}
        body = {'schema':ENV_UOP_TOOL_ROUTING_SCHEMA,'manifest_digest':policy['manifest_digest'],
            'action_name':spec.name,'action_schema_sha256':binding['schema_sha256'],'owner_skill':binding['owner_skill'],
            'project_id':context.project_id,'client_id':context.client_id,'request_id':context.request_id,
            'workflow_classes':classes,'entry_event':binding['entry_event'],'selected_pipeline':route,
            'selection_context': selection_context,
            'fallback_used':selection['fallback_used'],'effect_contract':declared,
            'gates_checked':sorted(required),
            'runtime_checks':['exact_policy_and_action_binding','connection_scope','project_binding',
                'current_permission','required_delta','current_execution_guard',
                'declared_same_contract_pipeline','observed_pre_invocation_readiness'],
            'execution_budget':guard.task.budget.model_dump(mode='json') if guard is not None else None,
            'task_mode':guard.mode_validation if guard is not None else None,
            'source_verification_owner':'owning_lane_handler','device_measurement_owner':'accelerators_and_owned_worker',
            'data_touch_allowed':True,'effect_executed':False,'native_task_attestation':'not_provided',
            'user_approval_inferred':False}
        return {**body,'decision_sha256':_digest(body)}

    @staticmethod
    def validate_result(value):
        from .lineage import visible_payload
        from .redaction import contains_secret
        visible_payload(value)
        if contains_secret(value):
            raise LaneError('ENV_UOP_DISCLOSURE_BLOCKED','The operation result contains raw credential material; inspect its owning output contract.')


def route_env_uop_data_touch(runtime,*,spec,context,arguments,selection):
    """Preserve the original route entrypoint at the current engine boundary."""
    return runtime.admit(spec,context,arguments,selection)


def env_uop_tool_routing_catalog():
    return {'schema':ENV_UOP_TOOL_ROUTING_SCHEMA,'selection_inputs':['exact_action_schema','project','client','lane',
        'workflow','phase','platform','grant','readiness','declared_pipeline','current_task_budget'],
        'uop_can_override_env':False,'uop_can_override_project':False,'user_approval_inferred':False,
        'selection_or_catalog_is_execution_evidence':False,
        'legacy_translation_layer':False}
