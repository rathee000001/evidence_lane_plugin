"""Persistent project-participant continuity with exact client ownership.

Replaces the base's shared PV/session JSON binding with one project's SQLite
history. Engine client authentication is verified; native host task identities
are not. Native-attestation requirements fail before a continuation is offered.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import Field

from .canon_task_graph import CanonContinuationCheckpoint, CanonStore
from .errors import LaneError
from .migrations import Migration, apply_migrations
from .plan_runtime import TASK_ID_PATTERN, content_digest
from .project_memory import DIGEST, MemoryReference, ProjectMemory
from .redaction import redact
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import LaneStore, json_text, now


class ContinuationOffer(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    participant_id: str = Field(pattern=UUID_PATTERN)
    destination_participant_id: str = Field(pattern=UUID_PATTERN)
    plan_revision: int = Field(ge=1)
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    contract_digest: str = Field(pattern=DIGEST)
    memory_checkpoint: str | None = Field(default=None, pattern=DIGEST)
    expires_in_seconds: int = Field(default=3600, ge=30, le=86400)
    require_native_attestation: bool = False


class ContinuationOffered(Contract):
    continuation_id: str
    continuation_digest: str
    source_client_id: str
    destination_client_id: str
    expires_at: str
    state: str = 'offered'
    identity_scope: str = 'authenticated_engine_clients'
    native_task_attestation: str = 'not_provided'
    host_task_created: bool = False
    canon_checkpoint_digest: str | None = Field(default=None, pattern=DIGEST)


class ContinuationRecoveryOffer(Contract):
    offer: ContinuationOffer
    expected_source_participant_digest: str = Field(pattern=DIGEST)
    expected_destination_participant_digest: str = Field(pattern=DIGEST)
    reason: str = Field(min_length=1, max_length=1000)


class ContinuationAccept(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    continuation_id: str = Field(pattern=UUID_PATTERN)
    continuation_digest: str = Field(pattern=DIGEST)


class ContinuationResult(Contract):
    continuation_id: str
    participant_id: str
    owner_generation: int
    owner_client_id: str
    previous_owner_client_id: str
    binding_digest: str
    plan_revision: int
    task_id: str
    transferred_participant_ids: list[str]
    source_client_closeout_only: bool = True
    plan_mutated: bool = False
    jobs_replayed: bool = False
    native_task_attestation: str = 'not_provided'
    host_session_attached: bool = False
    source_bytes_reverified: bool = False
    canon_checkpoint_digest: str | None = Field(default=None, pattern=DIGEST)
    canon_decisions_replayed: bool = False


class ContinuationCancel(ContinuationAccept):
    reason: str = Field(min_length=1, max_length=1000)


class ContinuationCancelled(Contract):
    continuation_id: str
    state: str = 'cancelled'


class ContinuationRead(Contract):
    participant_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    after_sequence: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)


class ContinuationPage(Contract):
    project_id: str
    offers: list[dict]
    last_sequence: int
    truncated: bool
    native_task_attestation: str = 'not_provided'
    scope: str = 'project_participant_ownership_and_context'


CONTINUATION_MIGRATIONS=(Migration('continuation',1,'Versioned participant bindings, destination-pinned offers and source closeout',(
    """CREATE TABLE continuation_offers (sequence INTEGER PRIMARY KEY, continuation_id TEXT NOT NULL UNIQUE,
       participant_id TEXT NOT NULL, source_client_id TEXT NOT NULL,
       destination_client_id TEXT NOT NULL, continuation_digest TEXT NOT NULL UNIQUE,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)), state TEXT NOT NULL CHECK(state IN ('offered','accepted','cancelled')),
       result_json TEXT CHECK(json_valid(result_json)), created_at TEXT NOT NULL)""",
    "CREATE UNIQUE INDEX continuation_one_offer ON continuation_offers(source_client_id) WHERE state='offered'",
    """CREATE TABLE continuation_bindings (binding_digest TEXT PRIMARY KEY,
       participant_id TEXT NOT NULL, generation INTEGER NOT NULL,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)), UNIQUE(participant_id,generation))""",
    """CREATE TABLE continuation_retired_clients (client_id TEXT PRIMARY KEY, continuation_id TEXT NOT NULL REFERENCES continuation_offers(continuation_id),
       destination_client_id TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE continuation_events (sequence INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
       kind TEXT NOT NULL, actor_id TEXT NOT NULL, input_digest TEXT NOT NULL,
       result_json TEXT NOT NULL CHECK(json_valid(result_json)), previous_digest TEXT, digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)""",
)),)


class TaskContinuity:
    def __init__(self,store,*,clock=None):
        self.project = store.project if isinstance(store, LaneStore) else store
        self.store = self.project.lane('chat_lineage')
        self.clock=clock or (lambda:datetime.now(UTC))
        self.canon=CanonStore(store)
        self.memory=ProjectMemory(store)

    def initialize(self,lease):
        self.canon.initialize(lease)
        apply_migrations(self.store,CONTINUATION_MIGRATIONS,writer=lease)

    def _participant(self, participant_id, *, owner=None):
        with self.canon.store.connection(read_only=True) as connection:
            return self.canon.participant(connection, participant_id, owner=owner)

    def _participants(self, owner_id):
        with self.canon.store.connection(read_only=True) as connection:
            return connection.execute('SELECT participant_id FROM canon_participants WHERE owner_client_id=? '
                                      'ORDER BY participant_id LIMIT 33', (owner_id,)).fetchall()

    def _plan_event_head(self):
        with self.project.lane('plan').connection(read_only=True) as connection:
            return connection.execute('SELECT event_head FROM plan_current WHERE singleton=1').fetchone()[0]

    @staticmethod
    def _event(connection,request_id,kind,actor_id,input_digest,result):
        prior=connection.execute('SELECT sequence,digest FROM continuation_events ORDER BY sequence DESC LIMIT 1').fetchone()
        body={'sequence':prior['sequence']+1 if prior else 1,'request_id':request_id,'kind':kind,'actor_id':actor_id,
              'input_digest':input_digest,'result_json':json_text(result),'previous_digest':prior['digest'] if prior else None,'created_at':now()}
        digest=content_digest(body)
        connection.execute('INSERT INTO continuation_events VALUES(?,?,?,?,?,?,?,?,?)',(body['sequence'],request_id,kind,actor_id,input_digest,
            body['result_json'],body['previous_digest'],digest,body['created_at']))

    @staticmethod
    def _replay(connection,request_id,kind,actor_id,input_digest):
        row=connection.execute('SELECT * FROM continuation_events WHERE request_id=?',(request_id,)).fetchone()
        if row:
            if (row['kind'],row['actor_id'],row['input_digest'])!=(kind,actor_id,input_digest):
                raise LaneError('CONTINUATION_REQUEST_CONFLICT','This request belongs to another continuation or actor.')
            body=dict(row)
            digest=body.pop('digest')
            if content_digest(body)!=digest:raise LaneError('CONTINUATION_HISTORY_INTEGRITY','Continuation history is inconsistent.')
            return json.loads(row['result_json'])
        return None

    def _boundary(self,connection,*,task_id,revision,digest):
        ref=MemoryReference(kind='plan_task',key=task_id,revision=revision,digest=digest)
        source=self.memory._reference(connection,ref)
        if self.memory._plan_head(connection)!=revision:
            raise LaneError('CONTINUATION_STALE_PLAN','Refresh the continuation after the Plan revision changes.')
        if source['source_state'] not in {'queued','active','blocked'}:
            raise LaneError('CONTINUATION_TASK_TERMINAL','Select unfinished work in the current Plan.')
        with self.project.lane('plan').connection(read_only=True) as plan:
            if self.memory._table(plan,'jobs_jobs') and plan.execute("SELECT 1 FROM jobs_jobs WHERE state IN ('queued','running','uncertain') LIMIT 1").fetchone():
                raise LaneError('CONTINUATION_WORK_NOT_QUIESCENT','Finish or checkpoint outstanding project jobs before transferring ownership.')
            if self.memory._table(plan,'jobs_effects') and plan.execute("SELECT 1 FROM jobs_effects WHERE state='prepared' LIMIT 1").fetchone():
                raise LaneError('CONTINUATION_EFFECT_UNCERTAIN','Reconcile uncertain effects before transferring ownership.')
            if self.memory._table(plan,'steer_requests') and plan.execute("SELECT 1 FROM steer_requests WHERE state IN ('pending','checkpointing','ready') LIMIT 1").fetchone():
                raise LaneError('CONTINUATION_STEER_PENDING','Apply or resolve pending steers before transferring ownership.')
        return ref.model_dump()

    def offer(self,request,lease,*,actor_id,administrative=None):
        if request.require_native_attestation:
            raise LaneError('NATIVE_TASK_ATTESTATION_UNAVAILABLE','The native host has not supplied an independently verified task binding. No continuation was offered.')
        self.initialize(lease)
        with lease.transaction('chat_lineage') as connection:
            key=content_digest(request.model_dump() if administrative is None else {'offer':request.model_dump(),'administrative':administrative})
            prior=self._replay(connection,request.request_id,'offer',actor_id,key)
            if prior:return ContinuationOffered(**prior)
            source=self._participant(request.participant_id,owner=None if administrative else actor_id)
            destination=self._participant(request.destination_participant_id)
            source_client=source['owner_client_id']
            if administrative and (content_digest(source)!=administrative['expected_source_participant_digest']
                    or content_digest(destination)!=administrative['expected_destination_participant_digest']):
                raise LaneError('RECOVERY_PARTICIPANT_CHANGED','Read the exact current source and destination participant identities before administrative reassignment.')
            if destination['owner_client_id']==source_client:
                raise LaneError('CONTINUATION_DESTINATION_REQUIRED','Choose a participant owned by another authenticated client.')
            task=self._boundary(connection,task_id=request.task_id,revision=request.plan_revision,digest=request.contract_digest)
            if connection.execute("SELECT 1 FROM continuation_offers WHERE source_client_id=? AND state='offered'",(source_client,)).fetchone():
                raise LaneError('CONTINUATION_ALREADY_OFFERED','Resolve the existing offer before creating another.')
            owned=self._participants(source_client)
            if len(owned)>32:
                raise LaneError('CONTINUATION_PARTICIPANT_BUDGET','This continuation exceeds the bounded participant ownership set.')
            source_participants=[{'participant_id':item[0],'digest':content_digest(self._participant(item[0],owner=source_client))} for item in owned]
            canon_checkpoint = self.canon.continuation_checkpoint([item[0] for item in owned]).model_dump()
            checkpoint=None
            if request.memory_checkpoint:
                with self.memory.store.connection(read_only=True) as memory:
                    row=memory.execute('SELECT body_json FROM memory_checkpoints WHERE checkpoint_digest=?',(request.memory_checkpoint,)).fetchone() if self.memory._table(memory,'memory_checkpoints') else None
                checkpoint=json.loads(row[0]) if row else None
                if (not checkpoint or content_digest(checkpoint)!=request.memory_checkpoint or checkpoint.get('project_id')!=self.store.project_id
                        or checkpoint.get('task')!=task or checkpoint.get('source_client_id')!=source_client):
                    raise LaneError('CONTINUATION_MEMORY_MISMATCH','Select a Memory checkpoint owned by this source client and exact Plan task.')
            identity=str(uuid4())
            body={'continuation_id':identity,'project_id':self.store.project_id,'source_root':str(self.store.source_root),'state_root':str(self.store.root),
                  'participant_id':request.participant_id,'source_client_id':source_client,'source_participant_digest':content_digest(source),
                  'source_owner_generation':source.get('owner_generation',1),'destination_participant_id':request.destination_participant_id,
                  'source_participants':source_participants,
                  'destination_participant_digest':content_digest(destination),'destination_client_id':destination['owner_client_id'],
                  'task':task,'memory_checkpoint':request.memory_checkpoint,'lineage_head':self.memory._lineage_head(connection),
                  'canon_checkpoint':canon_checkpoint,'canon_checkpoint_digest':content_digest(canon_checkpoint),
                  'plan_event_head':self._plan_event_head(),
                  'expires_at':(self.clock()+timedelta(seconds=request.expires_in_seconds)).isoformat(),'created_at':now(),
                  'identity_scope':'authenticated_engine_clients','native_task_attestation':'not_provided','source_bytes_reverified':False}
            if administrative:
                body['administrative_recovery']={**administrative,'authorized_by_client_id':actor_id}
            digest=content_digest(body)
            connection.execute("INSERT INTO continuation_offers(continuation_id,participant_id,source_client_id,destination_client_id,continuation_digest,body_json,state,created_at) VALUES(?,?,?,?,?,?,'offered',?)",
                (identity,request.participant_id,source_client,destination['owner_client_id'],digest,json_text(body),body['created_at']))
            result=ContinuationOffered(continuation_id=identity,continuation_digest=digest,source_client_id=source_client,
                                      destination_client_id=destination['owner_client_id'],expires_at=body['expires_at'],
                                      canon_checkpoint_digest=body['canon_checkpoint_digest'])
            self._event(connection,request.request_id,'offer',actor_id,key,result.model_dump())
            return result

    def _offer(self,connection,identity,digest,*,current_binding=True):
        row=connection.execute('SELECT * FROM continuation_offers WHERE continuation_id=?',(identity,)).fetchone()
        if row is None:raise LaneError('CONTINUATION_NOT_FOUND','The continuation is not recorded in this project.')
        body=json.loads(row['body_json'])
        if (row['continuation_digest']!=digest or content_digest(body)!=digest or body.get('project_id')!=self.store.project_id
                or (current_binding and (body['source_root']!=str(self.store.source_root) or body['state_root']!=str(self.store.root)))
                or (row['participant_id'],row['source_client_id'],row['destination_client_id'])
                   !=(body['participant_id'],body['source_client_id'],body['destination_client_id'])):
            raise LaneError('CONTINUATION_IDENTITY_MISMATCH','The stored continuation differs from its project and participant binding.')
        if 'canon_checkpoint' in body:
            checkpoint = CanonContinuationCheckpoint.model_validate(body['canon_checkpoint'])
            if (content_digest(checkpoint.model_dump()) != body.get('canon_checkpoint_digest')
                    or checkpoint.project_id != self.store.project_id
                    or checkpoint.participant_ids != [item['participant_id'] for item in body['source_participants']]):
                raise LaneError('CONTINUATION_CANON_INTEGRITY', 'The Canon checkpoint differs from its exact project and transferred participants.')
        return row,body

    def accept(self,request,lease,*,actor_id):
        self.initialize(lease)
        with lease.coordinated_transaction(['chat_lineage', 'canon']) as commit:
            connection = commit.connection('chat_lineage')
            key=content_digest(request.model_dump())
            prior=self._replay(connection,request.request_id,'accept',actor_id,key)
            if prior:return ContinuationResult(**prior)
            row,body=self._offer(connection,request.continuation_id,request.continuation_digest)
            if actor_id!=body['destination_client_id']:
                raise LaneError('CONTINUATION_RECEIVER_MISMATCH','Only the exact destination client can consume this offer.')
            if row['state']!='offered':raise LaneError('CONTINUATION_ALREADY_RESOLVED','This continuation has already been accepted or cancelled.')
            if datetime.fromisoformat(body['expires_at'])<=self.clock():raise LaneError('CONTINUATION_EXPIRED','Ask the source owner for a current offer.')
            source=self._participant(body['participant_id'],owner=body['source_client_id'])
            destination=self._participant(body['destination_participant_id'],owner=actor_id)
            if content_digest(source)!=body['source_participant_digest'] or content_digest(destination)!=body['destination_participant_digest']:
                raise LaneError('CONTINUATION_OWNERSHIP_CHANGED','A participant changed since the continuation was offered.')
            task=body['task']
            self._boundary(connection,task_id=task['key'],revision=task['revision'],digest=task['digest'])
            if self._plan_event_head()!=body['plan_event_head']:
                raise LaneError('CONTINUATION_PLAN_CHANGED','Plan activity changed after this offer; prepare a current continuation.')
            if self.memory._lineage_head(connection)!=body['lineage_head']:
                raise LaneError('CONTINUATION_LINEAGE_CHANGED','Visible project input changed; review it before preparing a current offer.')
            current_ids=[item[0] for item in self._participants(body['source_client_id'])]
            if current_ids!=[item['participant_id'] for item in body['source_participants']]:
                raise LaneError('CONTINUATION_OWNERSHIP_CHANGED','The source participant set changed after the offer.')
            if 'canon_checkpoint' not in body:
                raise LaneError('CONTINUATION_CANON_CHECKPOINT_REQUIRED', 'This historical offer has no sealed Canon context; cancel it and create a fresh offer.')
            if content_digest(self.canon.continuation_checkpoint(current_ids).model_dump()) != body['canon_checkpoint_digest']:
                raise LaneError('CONTINUATION_CANON_CHANGED', 'Canon activity changed after this offer; review it and create a fresh continuation.')
            binding_digest=None
            for selected in body['source_participants']:
                prior_owner=self._participant(selected['participant_id'],owner=body['source_client_id'])
                if content_digest(prior_owner)!=selected['digest']:
                    raise LaneError('CONTINUATION_OWNERSHIP_CHANGED','A source participant changed after the offer.')
                next_generation=prior_owner.get('owner_generation',1)+1
                owner={**prior_owner,'owner_client_id':actor_id,'owner_generation':next_generation,
                       'reported_host_task_id':destination.get('reported_host_task_id')}
                binding={'project_id':self.store.project_id,'participant_id':selected['participant_id'],'generation':next_generation,
                         'previous_participant':prior_owner,'current_participant':owner,'continuation_digest':request.continuation_digest,
                         'task':task,'source_client_id':body['source_client_id'],'destination_client_id':actor_id,'created_at':now(),
                         'canon_checkpoint_digest':body['canon_checkpoint_digest'],'canon_decisions_replayed':False,
                         'native_task_attestation':'not_provided','source_client_closeout_only':True}
                selected_digest=content_digest(binding)
                if selected['participant_id']==body['participant_id']:binding_digest=selected_digest
                connection.execute('INSERT INTO continuation_bindings VALUES(?,?,?,?)',(selected_digest,selected['participant_id'],next_generation,json_text(binding)))
                commit.connection('canon').execute('UPDATE canon_participants SET owner_client_id=?,body_json=?,digest=? WHERE participant_id=?',
                                   (actor_id,json_text(owner),content_digest(owner),selected['participant_id']))
            connection.execute('INSERT INTO continuation_retired_clients VALUES(?,?,?,?)',
                               (body['source_client_id'],request.continuation_id,actor_id,now()))
            result=ContinuationResult(continuation_id=request.continuation_id,participant_id=body['participant_id'],owner_generation=body['source_owner_generation']+1,
                owner_client_id=actor_id,previous_owner_client_id=body['source_client_id'],binding_digest=binding_digest,plan_revision=task['revision'],task_id=task['key'],
                transferred_participant_ids=current_ids,canon_checkpoint_digest=body['canon_checkpoint_digest'])
            connection.execute("UPDATE continuation_offers SET state='accepted',result_json=? WHERE continuation_id=?",(json_text(result.model_dump()),request.continuation_id))
            self._event(connection,request.request_id,'accept',actor_id,key,result.model_dump())
            self.store.append_receipt('project_participant_continued',result.model_dump(),connection=connection)
            return result

    def cancel(self,request,lease,*,actor_id,administrative=False):
        self.initialize(lease)
        with lease.transaction('chat_lineage') as connection:
            key=content_digest(request.model_dump() if not administrative else {'request':request.model_dump(),'administrative':True})
            prior=self._replay(connection,request.request_id,'cancel',actor_id,key)
            if prior:return ContinuationCancelled(**prior)
            row,body=self._offer(connection,request.continuation_id,request.continuation_digest,current_binding=not administrative)
            self._participant(body['participant_id'],owner=None if administrative else actor_id)
            if row['state']!='offered':raise LaneError('CONTINUATION_ALREADY_RESOLVED','Only an open continuation can be cancelled.')
            result=ContinuationCancelled(continuation_id=request.continuation_id)
            connection.execute("UPDATE continuation_offers SET state='cancelled' WHERE continuation_id=?",(request.continuation_id,))
            self._event(connection,request.request_id,'cancel',actor_id,key,result.model_dump())
            self.store.append_receipt('project_continuation_cancelled',{'continuation_id':request.continuation_id,'reason':redact(request.reason),
                'source_client_id':body['source_client_id'],'actor_id':actor_id,'administrative_recovery':administrative},connection=connection)
            return result

    def read(self,request=None):
        request=request or ContinuationRead()
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            if not self.memory._table(connection,'continuation_offers'):
                return ContinuationPage(project_id=self.store.project_id,offers=[],last_sequence=request.after_sequence,truncated=False)
            where,parameters='sequence>?',[request.after_sequence]
            if request.participant_id:
                where+=' AND participant_id=?'
                parameters.append(request.participant_id)
            rows=connection.execute('SELECT * FROM continuation_offers WHERE '+where+' ORDER BY sequence LIMIT ?',[*parameters,request.limit+1]).fetchall()
            selected=[]
            for row in rows[:request.limit]:
                _,body=self._offer(connection,row['continuation_id'],row['continuation_digest'],current_binding=False)
                item={'sequence':row['sequence'],'continuation_id':row['continuation_id'],'continuation_digest':row['continuation_digest'],
                      'state':row['state'],'expired':datetime.fromisoformat(body['expires_at'])<=self.clock(),'binding':body,
                      'current_roots_match':body['source_root']==str(self.store.source_root) and body['state_root']==str(self.store.root),
                      'result':json.loads(row['result_json']) if row['result_json'] else None}
                if row['state']=='accepted':
                    result=item['result']
                    binding=connection.execute('SELECT * FROM continuation_bindings WHERE binding_digest=?',(result.get('binding_digest') if result else None,)).fetchone()
                    prior=json.loads(binding['body_json']) if binding else None
                    if (not prior or content_digest(prior)!=result['binding_digest'] or prior['continuation_digest']!=row['continuation_digest']
                            or prior['participant_id']!=body['participant_id'] or prior['destination_client_id']!=result.get('owner_client_id')
                            or prior['generation']!=result.get('owner_generation') or result.get('native_task_attestation')!='not_provided'
                            or result.get('host_session_attached') is not False
                            or prior.get('canon_checkpoint_digest')!=body.get('canon_checkpoint_digest')
                            or result.get('canon_checkpoint_digest')!=body.get('canon_checkpoint_digest')
                            or result.get('canon_decisions_replayed',False) is not False):
                        raise LaneError('CONTINUATION_BINDING_INTEGRITY','The accepted continuation differs from its immutable ownership record.')
                if len(json_text([*selected,item]).encode())>262144:break
                selected.append(item)
            return ContinuationPage(project_id=self.store.project_id,offers=selected,last_sequence=selected[-1]['sequence'] if selected else request.after_sequence,truncated=len(selected)<len(rows))

    def verify_history(self,*,limit=50000):
        if not 1<=limit<=50000:raise LaneError('CONTINUATION_HISTORY_BUDGET','Use a bounded history verification limit.')
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            rows=connection.execute('SELECT * FROM continuation_events ORDER BY sequence LIMIT ?',(limit+1,)).fetchall() if self.memory._table(connection,'continuation_events') else []
            if len(rows)>limit:raise LaneError('CONTINUATION_HISTORY_BUDGET','The continuation history exceeds the selected budget.')
            previous=None
            for sequence,row in enumerate(rows,1):
                body=dict(row)
                digest=body.pop('digest')
                if row['sequence']!=sequence or row['previous_digest']!=previous or content_digest(body)!=digest:
                    raise LaneError('CONTINUATION_HISTORY_INTEGRITY','The continuation event chain is inconsistent.')
                previous=digest
            return {'events_verified':len(rows),'head':previous}


def register_continuation_actions(engine):
    def recover(context,request):
        if request.offer.require_native_attestation:
            raise LaneError('NATIVE_TASK_ATTESTATION_UNAVAILABLE','Administrative reassignment does not attest a native host task.')
        store=engine.directory.open(context.project_id,write=True)
        with engine.project_work.mutation(store) as lease:
            administrative=request.model_dump(exclude={'offer'})
            administrative['reason']=redact(administrative['reason'])
            return TaskContinuity(store).offer(request.offer,lease,actor_id=context.client_id,administrative=administrative)
    def cancel_recovery(context,request):
        store=engine.directory.open(context.project_id,write=True)
        with engine.project_work.mutation(store) as lease:
            return TaskContinuity(store).cancel(request,lease,actor_id=context.client_id,administrative=True)
    engine.registry.register(ActionSpec('continuation_recovery_offer',
        'Administratively offer exact participant reassignment after client-session loss; destination acceptance is still required.',
        ContinuationRecoveryOffer,ContinuationOffered,recover,permission='admin',profile='recovery',mutates=True, workflow='recover'))
    engine.registry.register(ActionSpec('continuation_recovery_cancel','Administratively cancel an exact stale, unconsumed continuation.',
        ContinuationCancel,ContinuationCancelled,cancel_recovery,permission='admin',profile='recovery',mutates=True, workflow='recover'))
    def mutation(method):
        def run(context,request):
            if method=='offer' and request.require_native_attestation:
                raise LaneError('NATIVE_TASK_ATTESTATION_UNAVAILABLE','The native host has not supplied an independently verified task binding. No continuation was offered.')
            store=engine.directory.open(context.project_id,write=True)
            with engine.project_work.mutation(store) as lease:
                return getattr(TaskContinuity(store),method)(request,lease,actor_id=context.client_id)
        return run
    for name,description,input_model,output_model,method in [
        ('continuation_offer','Offer exact project-participant continuity to another registered client at a safe work boundary.',ContinuationOffer,ContinuationOffered,'offer'),
        ('continuation_accept','Accept a destination-pinned project continuation and put the source client in read-only closeout.',ContinuationAccept,ContinuationResult,'accept'),
        ('continuation_cancel','Cancel an unconsumed continuation owned by this source client.',ContinuationCancel,ContinuationCancelled,'cancel')]:
        engine.registry.register(ActionSpec(name,description,input_model,output_model,mutation(method),permission='write',profile='continuity',mutates=True, workflow='state-travel'))
    engine.registry.register(ActionSpec('continuation_read','Read bounded project continuation bindings without reattaching host sessions.',ContinuationRead,ContinuationPage,
        lambda context,request:TaskContinuity(engine.directory.open(context.project_id)).read(request),profile='continuity',queryable_in_delta=True, workflow='state-travel'))
