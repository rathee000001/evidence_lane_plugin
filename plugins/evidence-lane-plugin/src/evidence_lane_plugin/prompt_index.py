"""Source-bound prompt entry records in the owning ChatLineage database.

Retains the original contiguous prompt indices, exact hash/source verification,
PROMPT/TURN locators and bounded status reads. Classification is a separate
append; it neither changes Plan nor promotes a captured message into authority.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import Field

from .errors import LaneError
from .flash_authority import SessionFlashAuthority
from .lineage import ChatLineage
from .migrations import Migration, apply_migrations
from .plan_runtime import PlanRead, PlanStore, TaskModeBinding, content_digest
from .redaction import redact_text
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import LaneStore, ProjectStore, json_text, now

_PROMPT_REFERENCE = re.compile(r'^PROMPT(?:\s*[:#]\s*|\s+)(?P<index>[1-9][0-9]*)$', re.IGNORECASE)
_TURN_REFERENCE = re.compile(r'^TURN(?:\s*[:#]\s*|\s+)(?P<turn>[A-Za-z0-9._:-]{1,128})$', re.IGNORECASE)

PROMPT_MIGRATIONS = (Migration('prompt',1,'Source-bound prompt sequence and separate explicit classification',(
    '''CREATE TABLE prompt_entries (
       source_event_id TEXT PRIMARY KEY REFERENCES lineage_events(event_id),client_id TEXT NOT NULL,
       reported_session_id TEXT NOT NULL,prompt_index INTEGER NOT NULL,source_cursor TEXT NOT NULL,
       entry_json TEXT NOT NULL CHECK(json_valid(entry_json)),previous_entry_digest TEXT,
       entry_digest TEXT NOT NULL UNIQUE,UNIQUE(client_id,reported_session_id,prompt_index))''',
    '''CREATE TABLE prompt_classifications (
       classification_id TEXT PRIMARY KEY,source_event_id TEXT NOT NULL REFERENCES prompt_entries(source_event_id),
       ordinal INTEGER NOT NULL,actor_id TEXT NOT NULL,previous_digest TEXT,
       classification_json TEXT NOT NULL CHECK(json_valid(classification_json)),
       classification_digest TEXT NOT NULL UNIQUE,UNIQUE(source_event_id,ordinal))''',
    'CREATE INDEX prompt_client_session ON prompt_entries(client_id,reported_session_id,prompt_index)',
)),)


def is_prompt_reference(value):
    return isinstance(value,str) and bool(_PROMPT_REFERENCE.fullmatch(value.strip()) or _TURN_REFERENCE.fullmatch(value.strip()))


class PromptIndex:
    def __init__(self,project):
        if not isinstance(project,(ProjectStore,LaneStore)):
            raise LaneError('PROMPT_PROJECT_REQUIRED','Select an actual project store; no global prompt-index directory is an authority.')
        self.project = project.project if isinstance(project,LaneStore) else project
        self.store = self.project.lane('chat_lineage')

    @staticmethod
    def _exists(connection):
        return connection.execute("SELECT 1 FROM sqlite_schema WHERE name='prompt_entries' AND type='table'").fetchone() is not None

    def _source(self,source_event_id,source_cursor,client_id):
        with self.store.connection(read_only=True) as connection:
            row = connection.execute('SELECT * FROM lineage_events WHERE event_id=?',(source_event_id,)).fetchone()
        if row is None or row['cursor']!=source_cursor or row['client_id']!=client_id or row['kind'] not in {'prompt','steer'}:
            raise LaneError('PROMPT_SOURCE_MISMATCH','Entry classification requires this exact client-owned captured prompt or steer.')
        ChatLineage.validate_row(row)
        raw = self.store.read_object(row['payload_digest'])
        payload = json.loads(raw)
        return dict(row),payload

    def record_entry(self,source,lease,*,client_id,registry):
        if lease.store.project_id!=self.project.project_id or lease.store.root!=self.project.root:
            raise LaneError('PROMPT_WRITER_MISMATCH','Use this project writer for prompt entry.')
        row,payload = self._source(source.event_id,source.cursor,client_id)
        session = row['reported_session_id'] or 'unreported-session'
        apply_migrations(self.store,PROMPT_MIGRATIONS,writer=lease)
        with lease.transaction('chat_lineage') as connection:
            existing = connection.execute('SELECT * FROM prompt_entries WHERE source_event_id=?',(source.event_id,)).fetchone()
            if existing:
                return self._validated(existing)
            previous = connection.execute('SELECT prompt_index,entry_digest FROM prompt_entries WHERE client_id=? AND reported_session_id=? ORDER BY prompt_index DESC LIMIT 1',
                (client_id,session)).fetchone()
            ordinal = previous['prompt_index']+1 if previous else 1
            flash = SessionFlashAuthority().verify(registry=registry)
            plan = PlanStore(self.project).snapshot(PlanRead(limit=1))
            text = payload.get('text')
            inferred=[];inference_state='source_text_unavailable'
            if isinstance(text,str) and not payload.get('truncated'):
                from .operating_modes import _infer_from_request
                inferred = _infer_from_request(text)
                inference_state='inferred_requires_agent_classification' if inferred else 'unclassified'
            body={'schema':'evidence-lane.prompt-entry.v4','project_id':self.project.project_id,
                'client_id':client_id,'source_event_id':source.event_id,'source_cursor':source.cursor,
                'source_payload_digest':source.payload_digest,'source_provenance':source.provenance,
                'reported_session_id':session,'reported_turn_id':row['reported_turn_id'],'prompt_index':ordinal,
                'previous_entry_digest':previous['entry_digest'] if previous else None,
                'flash_digest':flash.manifest_digest,'plan_revision':plan.revision,
                'root_reference_before_capture':lease.acquisition_root_pv,
                'classification_state':inference_state,'inferred_mode_ids':inferred,'intent':'unclassified',
                'focus':None,'next_action':None,'plan_changed':False,'effects_authorized':False,
                'source_content_is_instruction_authority':False,'native_task_attestation':'not_provided','recorded_at':now()}
            digest=content_digest(body)
            connection.execute('INSERT INTO prompt_entries VALUES(?,?,?,?,?,?,?,?)',
                (source.event_id,client_id,session,ordinal,source.cursor,json_text(body),body['previous_entry_digest'],digest))
            self.store.append_receipt('prompt_entry_recorded',{'source_event_id':source.event_id,'entry_digest':digest,
                'classification_state':inference_state,'plan_changed':False},connection=connection)
            return {**body,'entry_digest':digest}

    @staticmethod
    def _validated(row):
        body=json.loads(row['entry_json'])
        if (content_digest(body)!=row['entry_digest'] or body['source_cursor']!=row['source_cursor']
                or body['source_event_id']!=row['source_event_id'] or body['client_id']!=row['client_id']
                or body['reported_session_id']!=row['reported_session_id'] or body['prompt_index']!=row['prompt_index']
                or body['previous_entry_digest']!=row['previous_entry_digest']):
            raise LaneError('PROMPT_INDEX_RECORD_MISMATCH','The prompt entry differs from its exact source and hash binding.')
        return {**body,'entry_digest':row['entry_digest']}

    def classify(self,request,lease,*,client_id,registry):
        if lease.store.project_id != self.project.project_id or lease.store.root != self.project.root:
            raise LaneError('PROMPT_WRITER_MISMATCH', 'Use this project writer for source classification.')
        _,stored_payload=self._source(request.source_event_id,request.source_cursor,client_id)
        from .capture_routing import CAPTURE_ENVELOPE, resolve_visible_source
        payload = resolve_visible_source(stored_payload, request.source_text)
        sparse = CAPTURE_ENVELOPE in stored_payload
        if payload.get('truncated'):
            raise LaneError('PROMPT_SOURCE_TRUNCATED','Classify only a complete captured source; register an exact bounded source segment first.')
        with self.store.connection(read_only=True) as connection:
            if not self._exists(connection):
                raise LaneError('PROMPT_ENTRY_REQUIRED','Record the source-bound prompt entry before classifying it.')
            entry=connection.execute('SELECT * FROM prompt_entries WHERE source_event_id=?',(request.source_event_id,)).fetchone()
        if entry is None:
            raise LaneError('PROMPT_ENTRY_REQUIRED','This captured message has no source-bound entry.')
        exact=self._validated(entry)
        action=registry.get(request.next_action)
        if action.workflow!=request.workflow:
            raise LaneError('PROMPT_WORKFLOW_MISMATCH','The next action must belong to the selected first-class workflow.')
        if request.intent=='informational' and (action.mutates or action.requires_delta):
            raise LaneError('PROMPT_INFORMATIONAL_ROUTE_INVALID','An informational entry must select an informational read.')
        from .lanes import get_lane
        lanes=[get_lane(name).canonical_lane_id for name in request.lanes]
        if len(set(lanes))!=len(lanes):
            raise LaneError('PROMPT_LANE_DUPLICATE','Select each owning lane once.')
        from .operating_modes import classify_operating_modes
        mode=classify_operating_modes(payload.get('text',''),explicit_modes=request.explicit_modes,
            code_lane=request.code_lane,custom_modes=request.custom_modes) if request.explicit_modes else None
        if sparse and mode is not None:
            # The classifier's result normally echoes its input. Sparse policy
            # keeps the exact request digest and selected contracts instead.
            mode.pop('request', None)
            mode['request_omitted_by_capture_policy'] = True
        body={'schema':'evidence-lane.prompt-classification.v4','source_event_id':request.source_event_id,
            'source_cursor':request.source_cursor,'entry_digest':exact['entry_digest'],'actor_id':client_id,
            'intent':request.intent,'focus':redact_text(request.focus),'workflow':request.workflow,'next_action':request.next_action,
            'lanes':lanes,'mode_selection':mode,'classification_basis':'agent_interpretation_of_exact_visible_source',
            'plan_changed':False,'effects_authorized':False,'native_task_attestation':'not_provided'}
        if sparse:
            body.update(classification_basis='agent_interpretation_of_hash_verified_visible_source',
                        source_input_digest=stored_payload[CAPTURE_ENVELOPE]['input_digest'],
                        source_text_digest=stored_payload[CAPTURE_ENVELOPE]['text_digest'], source_payload_persisted=False)
        with lease.transaction('chat_lineage') as connection:
            existing=connection.execute('SELECT * FROM prompt_classifications WHERE classification_id=?',(request.classification_id,)).fetchone()
            if existing:
                stored=json.loads(existing['classification_json'])
                if any(stored.get(k)!=v for k,v in body.items()):
                    raise LaneError('PROMPT_CLASSIFICATION_CONFLICT','This classification ID belongs to a different source or interpretation.')
                return {**stored,'classification_digest':existing['classification_digest']}
            prior=connection.execute('SELECT ordinal,classification_digest FROM prompt_classifications WHERE source_event_id=? ORDER BY ordinal DESC LIMIT 1',
                (request.source_event_id,)).fetchone()
            body.update(classification_id=request.classification_id,ordinal=prior['ordinal']+1 if prior else 1,
                previous_digest=prior['classification_digest'] if prior else None,recorded_at=now())
            digest=content_digest(body)
            connection.execute('INSERT INTO prompt_classifications VALUES(?,?,?,?,?,?,?)',
                (request.classification_id,request.source_event_id,body['ordinal'],client_id,body['previous_digest'],json_text(body),digest))
            self.store.append_receipt('prompt_entry_classified',{'source_event_id':request.source_event_id,
                'classification_digest':digest,'intent':request.intent,'plan_changed':False},connection=connection)
        return {**body,'classification_digest':digest}

    def status(self,*,client_id,reported_session_id=None,limit=20,after_index=0):
        if type(limit) is not int or not 1<=limit<=50 or type(after_index) is not int or after_index<0:
            raise LaneError('PROMPT_INDEX_BUDGET','Use a bounded prompt entry page.')
        if after_index and reported_session_id is None:
            raise LaneError('PROMPT_INDEX_SESSION_REQUIRED', 'Prompt indices restart per reported session; select the exact session before paging by index.')
        with self.store.connection(read_only=True) as connection:
            if not self._exists(connection):return {'entries':[],'truncated':False,'project_id':self.project.project_id}
            where='client_id=?';params=[client_id]
            if reported_session_id is not None:where+=' AND reported_session_id=?';params.append(reported_session_id)
            if after_index:where+=' AND prompt_index>?';params.append(after_index)
            rows=connection.execute('SELECT * FROM prompt_entries WHERE '+where+' ORDER BY reported_session_id,prompt_index LIMIT ?',(*params,limit+1)).fetchall()
            entries=[]
            for row in rows[:limit]:
                item=self._validated(row)
                self._source(item['source_event_id'],item['source_cursor'],client_id)
                prior=connection.execute('SELECT entry_digest FROM prompt_entries WHERE client_id=? AND reported_session_id=? AND prompt_index=?',
                    (client_id,item['reported_session_id'],item['prompt_index']-1)).fetchone()
                if item['previous_entry_digest']!=(prior[0] if prior else None) or (item['prompt_index']>1 and prior is None):
                    raise LaneError('PROMPT_INDEX_CHAIN_MISMATCH','The prompt sequence has a missing or changed predecessor.')
                classification=connection.execute('SELECT classification_json,classification_digest FROM prompt_classifications WHERE source_event_id=? ORDER BY ordinal DESC LIMIT 1',
                    (item['source_event_id'],)).fetchone()
                if classification:
                    content=json.loads(classification['classification_json'])
                    if content_digest(content)!=classification['classification_digest']:
                        raise LaneError('PROMPT_CLASSIFICATION_MISMATCH','A classification differs from its source-bound digest.')
                    item['classification']={**content,'classification_digest':classification['classification_digest']}
                entries.append(item)
        return {'project_id':self.project.project_id,'entries':entries,'truncated':len(rows)>limit,
            'next_after_index': entries[-1]['prompt_index'] if reported_session_id is not None and entries else None,
            'pagination_requires_session': reported_session_id is None and len(rows)>limit,
            'plan_changed':False,'native_task_attestation':'not_provided'}

    def classification(self, binding):
        """Resolve an immutable reference and its chain, without selecting the latest."""
        with self.store.connection(read_only=True) as connection:
            if not self._exists(connection):
                raise LaneError('TASK_MODE_SOURCE_MISSING', 'The referenced project classification is unavailable.')
            row = connection.execute('SELECT * FROM prompt_classifications WHERE classification_id=?',
                (binding.classification_id,)).fetchone()
            if row is None or row['classification_digest'] != binding.classification_digest:
                raise LaneError('TASK_MODE_SOURCE_MISMATCH', 'The exact classification ID and digest are required.')
            # Walk only the selected source chain, with an explicit bound.
            if row['ordinal'] > 256:
                raise LaneError('TASK_MODE_HISTORY_BUDGET', 'Capture a new source segment after 256 classifications.')
            chain = connection.execute('SELECT * FROM prompt_classifications WHERE source_event_id=? AND ordinal<=? ORDER BY ordinal',
                (row['source_event_id'], row['ordinal'])).fetchall()
            previous = None
            for ordinal, item in enumerate(chain, 1):
                body = json.loads(item['classification_json'])
                if (content_digest(body) != item['classification_digest'] or item['ordinal'] != ordinal
                        or item['previous_digest'] != previous
                        or any(body.get(field) != item[field] for field in (
                            'classification_id', 'source_event_id', 'ordinal', 'actor_id', 'previous_digest'))):
                    raise LaneError('TASK_MODE_CLASSIFICATION_CHAIN', 'The classification history is missing or changed.')
                previous = item['classification_digest']
            if len(chain) != row['ordinal']:
                raise LaneError('TASK_MODE_CLASSIFICATION_CHAIN', 'The selected classification predecessor is missing.')
            content = json.loads(row['classification_json'])
            entry = connection.execute('SELECT * FROM prompt_entries WHERE source_event_id=?',
                (binding.source_event_id,)).fetchone()
            if entry is None:
                raise LaneError('TASK_MODE_SOURCE_MISSING', 'The selected classification source entry is missing.')
            exact = self._validated(entry)
        source, payload = self._source(binding.source_event_id, binding.source_cursor, content['actor_id'])
        if (content['source_event_id'] != binding.source_event_id or content['source_cursor'] != binding.source_cursor
                or content['entry_digest'] != exact['entry_digest'] or exact['source_payload_digest'] != source['payload_digest']
                or exact['project_id'] != self.project.project_id or payload.get('truncated')):
            raise LaneError('TASK_MODE_SOURCE_MISMATCH', 'The mode must reference the complete original project source.')
        from .capture_routing import CAPTURE_ENVELOPE
        if CAPTURE_ENVELOPE in payload:
            envelope = payload[CAPTURE_ENVELOPE]
            if (envelope['input_truncated'] or content.get('source_input_digest') != envelope['input_digest']
                    or content.get('source_text_digest') != envelope['text_digest']
                    or content.get('classification_basis') != 'agent_interpretation_of_hash_verified_visible_source'):
                raise LaneError('TASK_MODE_SOURCE_MISMATCH', 'Sparse classification requires the exact verified source hash.')
        return content, payload

    def resolve(self,*,client_id,reported_session_id,reference):
        if not is_prompt_reference(reference):raise LaneError('PROMPT_REFERENCE_INVALID','Use PROMPT <index> or TURN <reported-turn-id>.')
        prompt=_PROMPT_REFERENCE.fullmatch(reference.strip());turn=_TURN_REFERENCE.fullmatch(reference.strip())
        with self.store.connection(read_only=True) as connection:
            if not self._exists(connection):raise LaneError('PROMPT_REFERENCE_NOT_RESOLVED','No indexed prompts exist for this project.')
            clause='prompt_index=?' if prompt else "json_extract(entry_json,'$.reported_turn_id')=?"
            value=int(prompt.group('index')) if prompt else turn.group('turn')
            rows=connection.execute('SELECT * FROM prompt_entries WHERE client_id=? AND reported_session_id=? AND '+clause+' LIMIT 2',
                (client_id,reported_session_id,value)).fetchall()
        if len(rows)!=1:raise LaneError('PROMPT_REFERENCE_NOT_RESOLVED','The exact session-bound prompt reference is missing or ambiguous.')
        entry=self._validated(rows[0]);self._source(entry['source_event_id'],entry['source_cursor'],client_id)
        return {'reference':reference.strip(),'entry':entry,'restoration_authorized':False}


class EntryClassify(Contract):
    classification_id:str=Field(pattern=UUID_PATTERN)
    source_event_id:str=Field(pattern=UUID_PATTERN)
    source_cursor:str=Field(pattern=r'^[0-9a-f]{64}$')
    intent:Literal['informational','semantic','stop','work']
    focus:str=Field(min_length=1,max_length=1000)
    workflow:str=Field(min_length=1,max_length=64)
    next_action:str=Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    lanes:list[str]=Field(default_factory=list,max_length=32)
    explicit_modes:list[str]=Field(default_factory=list,max_length=16)
    custom_modes:list[dict]=Field(default_factory=list,max_length=8)
    code_lane:Literal['local_code','github_code']='local_code'
    source_text: str | None = Field(default=None, max_length=65536)


class EntryResult(Contract):
    entry:dict
    task_mode_binding: TaskModeBinding | None = None
    plan_changed:Literal[False]=False


class PromptRead(Contract):
    reported_session_id:str|None=Field(default=None,min_length=1,max_length=128)
    limit:int=Field(default=20,ge=1,le=50)
    after_index:int=Field(default=0,ge=0)


class PromptStatus(Contract):
    index:dict
    plan_changed:Literal[False]=False


def register_prompt_actions(engine):
    def classify(context,request):
        project=engine.directory.open(context.project_id,write=True)
        with engine.project_work.mutation(project,kind='capture') as lease:
            entry = PromptIndex(project).classify(request,lease,client_id=context.client_id,registry=engine.registry)
            mode = entry.get('mode_selection')
            binding = None
            if mode is not None and entry['intent'] in {'work', 'semantic'}:
                selection = mode['mode_governance']
                binding = TaskModeBinding(**{key:entry[key] for key in (
                    'classification_id','classification_digest','source_event_id','source_cursor')},
                    selection_sha256=selection['selection_sha256'],manifest_digest=selection['manifest_digest'])
            return EntryResult(entry=entry,task_mode_binding=binding)
    engine.registry.register(ActionSpec('task_classify','Record source-bound intent, focus, owning lanes and next workflow action; classification does not apply a Plan steer or authorize work.',
        EntryClassify,EntryResult,classify,permission='write',mutates=True,profile='chat_lineage',workflow='run-project-lifecycle'))
    engine.registry.register(ActionSpec('prompt_index_status','Read verified prompt entry indices and separate classifications for this exact client/project.',
        PromptRead,PromptStatus,lambda context,request:PromptStatus(index=PromptIndex(engine.directory.open(context.project_id)).status(
            client_id=context.client_id,reported_session_id=request.reported_session_id,limit=request.limit,after_index=request.after_index)),
        profile='chat_lineage',workflow='manage-project-sources',queryable_in_delta=True))
