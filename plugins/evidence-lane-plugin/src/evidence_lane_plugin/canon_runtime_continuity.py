"""Bounded, attributed project context for an accepted client continuation.

Adapts the base's pointer-neutral visible-lineage context. The persistent engine
holds project state; this read route neither replays a transcript nor consumes a
native host exit slip. Owner views remain independently attributed authorities.
"""
from __future__ import annotations

import json

from pydantic import Field

from .canon_task_graph import CanonRead, CanonStore
from .errors import LaneError
from .lineage import ChatLineage
from .plan_runtime import PlanStore, content_digest
from .project_memory import DIGEST, MemoryRehydrate, ProjectMemory
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import json_text, project_snapshot


class ContinuationContextRequest(Contract):
    continuation_id: str = Field(pattern=UUID_PATTERN)
    continuation_digest: str = Field(pattern=DIGEST)
    limit: int = Field(default=4,ge=1,le=8)


class ContinuationContext(Contract):
    project_id: str
    continuation_id: str
    source_client_id: str
    destination_client_id: str
    current_owner: dict
    plan_task: dict
    lineage_locators: list[dict]
    canon: dict
    canon_continuity: dict
    memory: dict | None
    source_root: str
    state_root: str
    native_task_attestation: str = 'not_provided'
    host_session_attached: bool = False
    source_bytes_reverified: bool = False
    jobs_replayed: bool = False
    snapshot_scope: str = 'one_published_root_pv_with_separate_owner_views'


def read_continuation_context(store,request):
    with project_snapshot(store.root):
        return _read_continuation_context(store, request)


def _read_continuation_context(store,request):
    from .task_binding_registry import TaskContinuity
    continuity=TaskContinuity(store)
    memory=ProjectMemory(store)
    canon=CanonStore(store)
    with continuity.store.connection(read_only=True) as connection:
        connection.execute('BEGIN')
        if not memory._table(connection,'continuation_offers'):
            raise LaneError('CONTINUATION_NOT_FOUND','No continuation is recorded in this project.')
        row,body=continuity._offer(connection,request.continuation_id,request.continuation_digest,current_binding=False)
        if row['state']!='accepted':
            raise LaneError('CONTINUATION_NOT_ACCEPTED','Only an accepted ownership transfer exposes continuation context.')
        result=json.loads(row['result_json']) if row['result_json'] else {}
        binding=connection.execute('SELECT body_json FROM continuation_bindings WHERE binding_digest=?',(result.get('binding_digest'),)).fetchone()
        document=json.loads(binding[0]) if binding else {}
        if not document or content_digest(document)!=result.get('binding_digest') or document.get('continuation_digest')!=request.continuation_digest:
            raise LaneError('CONTINUATION_BINDING_INTEGRITY','The continuation is missing its exact ownership receipt.')
        owner=continuity._participant(body['participant_id'])
        current=None
        with PlanStore(store).store.connection(read_only=True) as plan_connection:
            head=plan_connection.execute('SELECT revision,event_head FROM plan_current WHERE singleton=1').fetchone()
            task=plan_connection.execute('SELECT * FROM plan_tasks WHERE revision=? AND task_id=?',(head['revision'],body['task']['key'])).fetchone()
            if task:
                try:
                    view=PlanStore._view(task,connection=plan_connection)
                except (LaneError,ValueError):
                    raise LaneError('CONTINUATION_PLAN_INTEGRITY','The current Plan contract or dependencies differ from their stored identity.') from None
                current={'task_id':view.definition.task_id,'title':view.definition.title,'profile':view.definition.profile,
                         'state':view.state,'revision':head['revision'],'contract_digest':view.contract_digest}
        plan={'offered_task':body['task'],'current_task':current,'event_head':head['event_head'],
              'contract_compatible':bool(current and head['revision']==body['task']['revision'] and current['contract_digest']==body['task']['digest']),
              'execution_authorized':False}
        lineage=[]
        if memory._table(connection,'lineage_events'):
            for event in connection.execute('SELECT * FROM lineage_events ORDER BY sequence DESC LIMIT ?',(request.limit,)).fetchall():
                ChatLineage.validate_row(event)
                lineage.append({key:event[key] for key in ['event_id','sequence','cursor','kind','provenance','client_id','payload_digest','received_at']})
    # Each owner remains responsible for its integrity and history; source content
    # is not implicitly loaded into this handoff packet.
    canon_view=canon.read(CanonRead(participant_id=body['participant_id'],limit=request.limit)).model_dump()
    checkpoint=body.get('canon_checkpoint')
    current_canon=canon.verify_history()
    canon_continuity={'checkpoint_present':checkpoint is not None,
        'checkpoint_digest':body.get('canon_checkpoint_digest'),
        'sealed_event_head':checkpoint['event_head'] if checkpoint else None,
        'current_event_head':current_canon['head'],
        'canon_changed_since_offer':checkpoint['event_head']!=current_canon['head'] if checkpoint else None,
        'pending_at_offer':checkpoint['pending'][:request.limit] if checkpoint else [],
        'pending_count_at_offer':len(checkpoint['pending']) if checkpoint else None,
        'pending_truncated':len(checkpoint['pending'])>request.limit if checkpoint else False,
        'complete_checkpoint_action':'continuation_read',
        'restore_effect':'context_only','canon_decisions_replayed':False}
    memory_view=None
    if body['memory_checkpoint']:
        checkpoint=memory.rehydrate(MemoryRehydrate(checkpoint_digest=body['memory_checkpoint']),receiver_client_id=body['destination_client_id']).model_dump()
        memory_view={key:checkpoint[key] for key in ['checkpoint','plan_compatible','memory_head_changed','lineage_head_changed','native_task_attestation']}
        memory_view['locators']=checkpoint['locators'][:request.limit]
        memory_view['locators_truncated']=len(checkpoint['locators'])>request.limit
    with PlanStore(store).store.connection(read_only=True) as connection:
        after=connection.execute('SELECT revision,event_head FROM plan_current WHERE singleton=1').fetchone()
        if tuple(after)!=tuple(head) or content_digest(continuity._participant(body['participant_id']))!=content_digest(owner):
            raise LaneError('CONTINUATION_CONTEXT_CHANGED','Plan activity changed while reading context; request a fresh bounded view.')
    result=ContinuationContext(project_id=store.project_id,continuation_id=request.continuation_id,
        source_client_id=body['source_client_id'],destination_client_id=body['destination_client_id'],current_owner=owner,
        plan_task=plan,lineage_locators=lineage,canon=canon_view,canon_continuity=canon_continuity,
        memory=memory_view,source_root=str(store.source_root),state_root=str(store.root))
    if len(json_text(result.model_dump()).encode())>262144:
        raise LaneError('CONTINUATION_CONTEXT_BUDGET','Request fewer context items to remain within the read budget.')
    return result


def register_continuation_context(engine):
    engine.registry.register(ActionSpec('continuation_context','Read attributed Plan, task exchange authority, Memory and lineage locators for an accepted project continuation.',
        ContinuationContextRequest,ContinuationContext,lambda context,request:read_continuation_context(engine.directory.open(context.project_id),request),
        profile='continuity',queryable_in_delta=True, workflow='handoff-project-work'))
