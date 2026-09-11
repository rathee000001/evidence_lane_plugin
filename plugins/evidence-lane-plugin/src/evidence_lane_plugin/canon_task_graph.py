"""Attributed task exchange authority exchanges and receiver-owned contracts in the task exchange authority lane.

Retains immutable envelopes, expected-input matching, corrections and results
from the base task exchange authority owner. Engine project participants are not native host task
attestations. task exchange authority admission never grants tools, changes Plan or promotes work.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4, uuid5

from pydantic import Field, model_validator

from .errors import LaneError
from .lanes import lane_schema_asset
from .migrations import Migration, apply_migrations, verify_schema_history_files
from .plan_runtime import content_digest
from .project_memory import DIGEST, MemoryReference, ProjectMemory
from .redaction import contains_secret, redact
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import LaneStore, json_text, now, reject_links

CanonKind = Literal['requirements', 'evidence', 'correction', 'plan_proposal', 'result', 'clarification', 'backfire']
CanonState = Literal['sealed', 'received', 'admitted', 'rejected', 'needs_clarification', 'superseded']
ScalarKind = Literal['string', 'integer', 'number', 'boolean', 'null']
Scalar = str | int | float | bool | None


def safe_text(value):
    if contains_secret(value) or redact(value) != value:
        raise ValueError('Secret material cannot enter a task exchange authority exchange')
    if isinstance(value, dict) and any(key.lower() in {'chain_of_thought','private_reasoning','hidden_reasoning','internal_reasoning','reasoning_content'} for key in value):
        raise ValueError('Only visible content can enter task exchange authority')


class CanonJoin(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    label: str = Field(min_length=1, max_length=200)
    reported_host_task_id: str | None = Field(default=None, pattern=UUID_PATTERN)

    @model_validator(mode='after')
    def visible(self):
        safe_text(self.label)
        return self


class CanonParticipant(Contract):
    project_id: str
    participant_id: str
    owner_client_id: str
    label: str
    reported_host_task_id: str | None
    owner_generation: int = Field(default=1, ge=1)
    identity_evidence: Literal['engine_client_ownership_with_optional_host_claim'] = 'engine_client_ownership_with_optional_host_claim'
    native_task_attestation: Literal['not_provided'] = 'not_provided'
    host_task_created: bool = False


class CanonTaskEndpoint(Contract):
    project_id: str = Field(pattern=UUID_PATTERN)
    participant_id: str = Field(pattern=UUID_PATTERN)


class CanonExpected(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    receiver_id: str = Field(pattern=UUID_PATTERN)
    contract_key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    expected_version: int = Field(default=0, ge=0)
    sender_ids: list[str] = Field(default_factory=list, max_length=32)
    sender_endpoints: list[CanonTaskEndpoint] = Field(default_factory=list, max_length=32)
    kinds: list[CanonKind] = Field(min_length=1, max_length=7)
    fields: dict[str, ScalarKind] = Field(default_factory=dict, max_length=32)
    required_reference_kinds: list[str] = Field(default_factory=list, max_length=6)
    max_bytes: int = Field(default=32768, ge=1024, le=65536)
    auto_admit: bool = False
    active: bool = True

    @model_validator(mode='after')
    def bounded_schema(self):
        endpoints = [(item.project_id, item.participant_id) for item in self.sender_endpoints]
        if not 1 <= len(self.sender_ids) + len(endpoints) <= 32 or len(set(endpoints)) != len(endpoints):
            raise ValueError('Select one to 32 distinct local participants or exact project endpoints')
        if len(set(self.sender_ids)) != len(self.sender_ids) or any(not re.fullmatch(UUID_PATTERN,key) for key in self.sender_ids):
            raise ValueError('Select distinct project participants')
        if len(set(self.kinds)) != len(self.kinds) or any(not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',key) for key in self.fields):
            raise ValueError('Use bounded unique kinds and named scalar fields')
        supported={'plan_task','lineage_event','learning_version','receipt','source_object','canon_exchange'}
        if not set(self.required_reference_kinds) <= supported:
            raise ValueError('Use supported evidence reference kinds')
        return self


class CanonContractResult(Contract):
    contract_digest: str
    version: int
    contract: dict


class CanonPayload(Contract):
    summary: str = Field(min_length=1, max_length=4000)
    fields: dict[str, Scalar] = Field(default_factory=dict, max_length=32)
    references: list[MemoryReference] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def bounded_visible_payload(self):
        if (len(self.model_dump_json().encode()) > 65536
                or any(not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',key) for key in self.fields)
                or any(isinstance(v,str) and len(v)>4000 or isinstance(v,float) and not math.isfinite(v) for v in self.fields.values())):
            raise ValueError('Use bounded named scalar fields and source references')
        safe_text(self.summary)
        safe_text(self.fields)
        return self


BackfireClass = Literal['EXECUTION_FAILURE_REQUIRES_UPSTREAM_ACTION',
    'MISSING_INFORMATION_FROM_SOURCE', 'NEW_REQUIREMENT_FROM_SOURCE', 'LINKED_TASK_INPUT_REQUIRED']


class CanonBackfireDetails(Contract):
    admitted_exchange_id: str = Field(pattern=UUID_PATTERN)
    admitted_envelope_digest: str = Field(pattern=DIGEST)
    failure_class: BackfireClass
    requested_revision: int = Field(ge=2, le=1000000)
    dependency_ids: list[str] = Field(default_factory=list, max_length=32)
    return_route: CanonTaskEndpoint
    trace: list[CanonTaskEndpoint] = Field(default_factory=list, max_length=32)
    automatic_retry_allowed: Literal[False] = False

    @model_validator(mode='after')
    def bounded_routes(self):
        if (len(set(self.dependency_ids)) != len(self.dependency_ids)
                or any(not re.fullmatch(UUID_PATTERN, key) for key in self.dependency_ids)):
            raise ValueError('Dependencies must name distinct bounded task exchange authority exchanges or task edges')
        nodes = [(item.project_id, item.participant_id) for item in self.trace]
        if len(set(nodes)) != len(nodes):
            raise ValueError('A correction trace cannot repeat a project participant')
        return self


class CanonSend(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    sender_id: str = Field(pattern=UUID_PATTERN)
    receiver_id: str = Field(pattern=UUID_PATTERN)
    destination_project_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    kind: CanonKind
    payload: CanonPayload
    expected_contract: str | None = Field(default=None, pattern=DIGEST)
    return_contract: str | None = Field(default=None, pattern=DIGEST)
    reply_to: str | None = Field(default=None, pattern=UUID_PATTERN)
    supersedes: str | None = Field(default=None, pattern=UUID_PATTERN)
    expires_at: str | None = Field(default=None, max_length=40)
    edge_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    backfire: CanonBackfireDetails | None = None

    @model_validator(mode='after')
    def exact_route(self):
        if self.sender_id == self.receiver_id and self.destination_project_id is None:
            raise ValueError('Select another project participant')
        if self.kind in {'result','clarification','backfire'} and not self.reply_to and not (self.kind == 'backfire' and self.backfire):
            raise ValueError('A return requires an exact earlier exchange')
        if self.backfire and (self.kind != 'backfire' or self.reply_to or self.supersedes or self.edge_id or not self.expected_contract):
            raise ValueError('A typed backfire uses its source-owned admitted locator and exact requested contract')
        if (self.kind == 'correction') != bool(self.supersedes):
            raise ValueError('A correction requires the exact exchange it revises')
        if self.expires_at:
            stamp=datetime.fromisoformat(self.expires_at)
            if stamp.tzinfo is None:
                raise ValueError('Expiry must include a timezone')
        return self


class CanonSent(Contract):
    exchange_id: str
    envelope_digest: str
    state: CanonState
    duplicate: bool = False
    local_role: Literal['outbox', 'inbox', 'both'] = 'both'
    artifact_path: str | None = None
    compatibility_reasons: list[str] = Field(default_factory=list)
    receiver_decision_required: bool = False
    authority: Literal['canon'] = 'canon'
    source_write_granted: bool = False
    plan_mutated: bool = False


class CanonTaskResult(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    edge_id: str = Field(pattern=UUID_PATTERN)
    reply_to: str = Field(pattern=UUID_PATTERN)
    payload: CanonPayload
    expires_at: str | None = Field(default=None, max_length=40)


class CanonBackfire(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    admitted_exchange_id: str = Field(pattern=UUID_PATTERN)
    admitted_envelope_digest: str = Field(pattern=DIGEST)
    failure_class: BackfireClass
    recipient: CanonTaskEndpoint
    requested_contract: str = Field(pattern=DIGEST)
    requested_revision: int = Field(ge=2, le=1000000)
    payload: CanonPayload
    dependency_ids: list[str] = Field(default_factory=list, max_length=32)
    return_route: CanonTaskEndpoint
    return_contract: str = Field(pattern=DIGEST)
    trace: list[CanonTaskEndpoint] = Field(default_factory=list, max_length=30)
    expires_at: str = Field(min_length=1, max_length=40)

    @model_validator(mode='after')
    def bounded_request(self):
        CanonBackfireDetails.model_validate(self.model_dump(include={
            'admitted_exchange_id','admitted_envelope_digest','failure_class','requested_revision',
            'dependency_ids','return_route','trace'}))
        if datetime.fromisoformat(self.expires_at).tzinfo is None:
            raise ValueError('Backfire expiry must include a timezone')
        return self


class CanonOperationSent(CanonSent):
    dedup_key: str = Field(pattern=DIGEST)
    automatic_retry_allowed: Literal[False] = False


class CanonReceive(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    source_project_id: str = Field(pattern=UUID_PATTERN)
    exchange_id: str = Field(pattern=UUID_PATTERN)
    envelope_digest: str = Field(pattern=DIGEST)


class CanonClassification(Contract):
    project_id: str
    message_digest: str
    classification: Literal['expected', 'undefined_or_incompatible']
    reasons: list[str]
    expected_contract: str | None
    observed_at: str
    automatic_admission_permitted: bool
    receiver_decision_required: bool
    admission_performed: Literal[False] = False
    project_mutated: Literal[False] = False
    source_write_granted: Literal[False] = False
    native_task_attestation: Literal['not_provided'] = 'not_provided'


class CanonPacketClassification(CanonClassification):
    source_project_id: str
    exchange_id: str
    envelope_digest: str


class CanonDecide(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    exchange_id: str = Field(pattern=UUID_PATTERN)
    envelope_digest: str = Field(pattern=DIGEST)
    expected_version: int = Field(ge=1)
    decision: Literal['admit','reject','clarify']
    reason: str = Field(min_length=1, max_length=1000)
    incompatible_input_decision: Literal['ACCEPT'] | None = None

    @model_validator(mode='after')
    def visible_reason(self):
        safe_text(self.reason)
        return self


class CanonDecisionResult(Contract):
    exchange_id: str
    state: CanonState
    version: int
    receiver_id: str
    reason: str | None = Field(default=None, max_length=1000)
    incompatible_input_decision: Literal['ACCEPT'] | None = None
    compatibility_reasons: list[str] = Field(default_factory=list)
    plan_mutated: bool = False
    source_write_granted: bool = False


class CanonSupersede(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    exchange_id: str = Field(pattern=UUID_PATTERN)
    envelope_digest: str = Field(pattern=DIGEST)
    expected_version: int = Field(ge=1)
    successor_id: str = Field(pattern=UUID_PATTERN)
    successor_envelope_digest: str = Field(pattern=DIGEST)


class CanonSuperseded(Contract):
    exchange_id: str
    envelope_digest: str
    successor_id: str
    successor_envelope_digest: str
    receiver_id: str
    version: int
    decision_basis: Literal['receiver_explicit', 'receiver_expected_contract', 'receiver_correction_admission']
    state: Literal['superseded'] = 'superseded'
    duplicate: bool = False
    plan_mutated: Literal[False] = False
    source_write_granted: Literal[False] = False


class CanonRead(Contract):
    participant_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    after_sequence: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)
    include_payload: bool = False
    max_bytes: int = Field(default=131072, ge=131072, le=262144)


class CanonPage(Contract):
    project_id: str
    participants: list[dict]
    contracts: list[dict]
    exchanges: list[dict]
    last_sequence: int
    truncated: bool
    participants_truncated: bool
    contracts_truncated: bool
    authority: Literal['canon'] = 'canon'
    native_task_attestation: Literal['not_provided'] = 'not_provided'


class CanonContinuationLocator(Contract):
    sequence: int = Field(ge=1)
    exchange_id: str = Field(pattern=UUID_PATTERN)
    envelope_digest: str = Field(pattern=DIGEST)
    source_project_id: str = Field(pattern=UUID_PATTERN)
    destination_project_id: str = Field(pattern=UUID_PATTERN)
    sender_id: str = Field(pattern=UUID_PATTERN)
    receiver_id: str = Field(pattern=UUID_PATTERN)
    state: Literal['sealed', 'received', 'needs_clarification']
    version: int = Field(ge=1)
    expected_contract: str | None = Field(pattern=DIGEST)


class CanonContinuationCheckpoint(Contract):
    schema_version: Literal[1] = 1
    project_id: str = Field(pattern=UUID_PATTERN)
    participant_ids: list[str] = Field(min_length=1, max_length=32)
    event_sequence: int = Field(ge=0)
    event_head: str | None = Field(pattern=DIGEST)
    pending: list[CanonContinuationLocator] = Field(max_length=256)
    decision_tokens_carried: Literal[False] = False
    canon_state_mutated: Literal[False] = False
    native_task_attestation: Literal['not_provided'] = 'not_provided'

    @model_validator(mode='after')
    def bounded_identity(self):
        if (self.participant_ids != sorted(set(self.participant_ids))
                or any(not re.fullmatch(UUID_PATTERN, item) for item in self.participant_ids)
                or len(self.model_dump_json().encode()) > 131072):
            raise ValueError('Use an exact bounded participant set and task exchange authority locator checkpoint')
        return self


class CanonInbox(Contract):
    receiver_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    states: list[CanonState] = Field(default_factory=list, max_length=6)
    exchange_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    after_sequence: int = Field(default=0, ge=0)
    after_event_sequence: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)
    events_per_packet: int = Field(default=50, ge=1, le=500)
    history_limit: int = Field(default=50000, ge=1, le=50000)
    max_bytes: int = Field(default=131072, ge=16384, le=1048576)


class CanonInboxPage(Contract):
    project_id: str
    receiver: CanonParticipant | None
    packets: list[dict]
    packet_count: int
    last_sequence: int
    truncated: bool
    history: dict
    projection_digest: str
    raw_payload_returned: Literal[False] = False
    project_mutated: Literal[False] = False
    native_task_attestation: Literal['not_provided'] = 'not_provided'


class CanonInspect(Contract):
    record_limit: int = Field(default=50000, ge=1, le=50000)


class CanonInspection(Contract):
    project_id: str
    initialized: bool
    counts: dict[str, int]
    packet_states: dict[str, int]
    integrity: list[str]
    foreign_key_errors: list[list]
    history: dict
    applied_migrations: list[dict]
    schema_contract: dict
    objects_verified: dict[str, int]
    historical_supersession_links_unavailable: int
    verified_scope: list[str]
    unverified_scope: list[str]
    projection_digest: str
    raw_payload_returned: Literal[False] = False
    project_mutated: Literal[False] = False
    native_task_attestation: Literal['not_provided'] = 'not_provided'


class CanonTaskEdgeRegister(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    edge_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    source_id: str = Field(pattern=UUID_PATTERN)
    destination: CanonTaskEndpoint
    direction: Literal['upstream', 'downstream', 'lateral'] = 'downstream'
    contract_digest: str = Field(pattern=DIGEST)
    schema_digest: str = Field(pattern=DIGEST)
    expected_return_contract: str = Field(pattern=DIGEST)
    edge_revision: int = Field(default=1, ge=1)
    dependency_ids: list[str] = Field(default_factory=list, max_length=32)
    task_mode: Literal['top_level_task', 'subagent'] = 'top_level_task'
    scope_class: Literal['read_only', 'governed_read_write'] = 'read_only'
    permitted_actions: list[str] = Field(default_factory=list, max_length=32)
    permitted_paths: list[str] = Field(default_factory=list, max_length=32)
    permitted_tools: list[str] = Field(default_factory=list, max_length=32)
    expires_at: str | None = Field(default=None, max_length=40)

    @model_validator(mode='after')
    def bounded_edge(self):
        for values in (self.dependency_ids, self.permitted_actions, self.permitted_paths, self.permitted_tools):
            if len(set(values)) != len(values) or any(not value.strip() or len(value) > 512 for value in values):
                raise ValueError('Use distinct bounded edge dependencies and scope entries')
            safe_text(values)
        if any(not re.fullmatch(UUID_PATTERN, value) for value in self.dependency_ids) or self.edge_id in self.dependency_ids:
            raise ValueError('Select other exact edge IDs as dependencies')
        if self.expires_at and datetime.fromisoformat(self.expires_at).tzinfo is None:
            raise ValueError('Expiry must include a timezone')
        return self


class CanonTaskEdgeBind(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    source_project_id: str = Field(pattern=UUID_PATTERN)
    edge_id: str = Field(pattern=UUID_PATTERN)
    edge_digest: str = Field(pattern=DIGEST)


class CanonTaskEdgeResult(Contract):
    project_id: str
    edge_id: str
    edge_digest: str
    edge: dict
    artifact_path: str
    local_role: Literal['source', 'destination', 'both']
    destination_bound: bool
    duplicate: bool = False
    contract_hash_evidence: Literal['declared_scope_verified_when_used_by_exchange'] = 'declared_scope_verified_when_used_by_exchange'
    source_write_granted: Literal[False] = False
    plan_mutated: Literal[False] = False
    host_task_created: Literal[False] = False
    native_task_attestation: Literal['not_provided'] = 'not_provided'


class CanonTaskGraphRead(Contract):
    record_limit: int = Field(default=5000, ge=1, le=50000)
    max_bytes: int = Field(default=262144, ge=16384, le=1048576)


class CanonTaskGraph(Contract):
    project_id: str
    nodes: list[str]
    edges: list[dict]
    missing_returns: list[dict]
    history: dict
    graph_digest: str
    cycle_check_scope: Literal['all_edges_recorded_in_selected_project'] = 'all_edges_recorded_in_selected_project'
    cross_project_graph_complete: Literal[False] = False
    fan_in_supported: Literal[True] = True
    fan_out_supported: Literal[True] = True
    cycles_allowed: Literal[False] = False
    project_mutated: Literal[False] = False
    native_task_attestation: Literal['not_provided'] = 'not_provided'


CANON_MIGRATIONS=(Migration('canon',1,'Client-owned participants, expected contracts and immutable exchange history',(
    """CREATE TABLE canon_participants (participant_id TEXT PRIMARY KEY, owner_client_id TEXT NOT NULL,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL, created_at TEXT NOT NULL)""",
    'CREATE INDEX canon_participant_owner ON canon_participants(owner_client_id)',
    """CREATE TABLE canon_contracts (contract_digest TEXT PRIMARY KEY, receiver_id TEXT NOT NULL REFERENCES canon_participants(participant_id),
       contract_key TEXT NOT NULL, version INTEGER NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json)),
       UNIQUE(receiver_id,contract_key,version))""",
    """CREATE TABLE canon_contract_current (receiver_id TEXT NOT NULL REFERENCES canon_participants(participant_id),
       contract_key TEXT NOT NULL, contract_digest TEXT NOT NULL REFERENCES canon_contracts(contract_digest),
       PRIMARY KEY(receiver_id,contract_key))""",
    """CREATE TABLE canon_exchanges (sequence INTEGER PRIMARY KEY, exchange_id TEXT NOT NULL UNIQUE,
       sender_id TEXT NOT NULL REFERENCES canon_participants(participant_id), receiver_id TEXT NOT NULL REFERENCES canon_participants(participant_id),
       kind TEXT NOT NULL, envelope_digest TEXT NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json)),
       state TEXT NOT NULL CHECK(state IN ('received','admitted','rejected','needs_clarification','superseded')),
       version INTEGER NOT NULL, state_digest TEXT NOT NULL, parent_id TEXT REFERENCES canon_exchanges(exchange_id), created_at TEXT NOT NULL)""",
    'CREATE INDEX canon_inbox ON canon_exchanges(receiver_id,sequence)',
    'CREATE INDEX canon_outbox ON canon_exchanges(sender_id,sequence)',
    """CREATE TABLE canon_events (sequence INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
       actor_id TEXT NOT NULL, input_digest TEXT NOT NULL, result_json TEXT NOT NULL CHECK(json_valid(result_json)),
       previous_digest TEXT, digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)""",
)),Migration('canon',2,'Address supersession events independently of their JSON payload',(
    """CREATE TABLE canon_supersessions (
       exchange_id TEXT PRIMARY KEY REFERENCES canon_exchanges(exchange_id),
       request_id TEXT NOT NULL UNIQUE REFERENCES canon_events(request_id))""",
)),Migration('canon',3,'Immutable task edges, named lane files and receiver-owned destination bindings',(
    """CREATE TABLE canon_task_edges (
       sequence INTEGER PRIMARY KEY, edge_id TEXT NOT NULL UNIQUE, edge_digest TEXT NOT NULL UNIQUE,
       source_node TEXT NOT NULL, destination_node TEXT NOT NULL,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)), artifact_path TEXT NOT NULL UNIQUE)""",
    """CREATE TABLE canon_task_edge_bindings (
       edge_id TEXT PRIMARY KEY REFERENCES canon_task_edges(edge_id),
       request_id TEXT NOT NULL UNIQUE REFERENCES canon_events(request_id))""",
)),Migration('canon',4,'Project-attributed outbox and inbox exchange roles without foreign participant replication',(
    'CREATE TABLE canon_exchange_migration4 AS SELECT * FROM canon_exchanges',
    'CREATE TABLE canon_supersession_migration4 AS SELECT * FROM canon_supersessions',
    'DROP TABLE canon_supersessions',
    'DROP TABLE canon_exchanges',
    """CREATE TABLE canon_exchanges (sequence INTEGER PRIMARY KEY, exchange_id TEXT NOT NULL UNIQUE,
       sender_id TEXT NOT NULL, receiver_id TEXT NOT NULL,
       kind TEXT NOT NULL, envelope_digest TEXT NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json)),
       state TEXT NOT NULL CHECK(state IN ('sealed','received','admitted','rejected','needs_clarification','superseded')),
       version INTEGER NOT NULL, state_digest TEXT NOT NULL, parent_id TEXT REFERENCES canon_exchanges(exchange_id), created_at TEXT NOT NULL,
       source_project_id TEXT NOT NULL, destination_project_id TEXT NOT NULL,
       local_role TEXT NOT NULL CHECK(local_role IN ('outbox','inbox','both')), artifact_path TEXT UNIQUE)""",
    """INSERT INTO canon_exchanges SELECT *,json_extract(body_json,'$.project_id'),
       json_extract(body_json,'$.project_id'),'both',NULL FROM canon_exchange_migration4 ORDER BY sequence""",
    'CREATE INDEX canon_inbox ON canon_exchanges(destination_project_id,receiver_id,sequence)',
    'CREATE INDEX canon_outbox ON canon_exchanges(source_project_id,sender_id,sequence)',
    """CREATE TABLE canon_supersessions (
       exchange_id TEXT PRIMARY KEY REFERENCES canon_exchanges(exchange_id),
       request_id TEXT NOT NULL UNIQUE REFERENCES canon_events(request_id))""",
    'INSERT INTO canon_supersessions SELECT * FROM canon_supersession_migration4',
    'DROP TABLE canon_supersession_migration4',
    'DROP TABLE canon_exchange_migration4',
)),Migration('canon',5,'Content-derived task-result and backfire identities within the attributable event history',(
    """CREATE UNIQUE INDEX canon_operation_dedup ON canon_events(kind,json_extract(result_json,'$.operation.dedup_key'))
       WHERE kind IN ('task_result','backfire')""",
)),)


class CanonStore:
    def __init__(self,store):
        self.project = store.project if isinstance(store, LaneStore) else store
        self.store = self.project.lane('canon')

    @staticmethod
    def _node_key(endpoint):
        return endpoint['project_id'] + ':' + endpoint['participant_id']

    @staticmethod
    def _would_cycle(existing_edges, source_node, destination_node):
        graph = {}
        for source, destination in existing_edges:
            graph.setdefault(source, set()).add(destination)
        stack, seen = [destination_node], set()
        while stack:
            current = stack.pop()
            if current == source_node:
                return True
            if current not in seen:
                seen.add(current)
                stack.extend(graph.get(current, ()))
        return False

    def _edge_path(self, edge_id, digest):
        if not re.fullmatch(UUID_PATTERN, edge_id) or not re.fullmatch(DIGEST, digest):
            raise LaneError('CANON_EDGE_INTEGRITY', 'The edge has an invalid bounded identity.')
        path = self.store.files / 'graph' / digest / (edge_id + '.json')
        reject_links(path, self.store.root)
        return path

    def _edge(self, connection, edge_id):
        if not self._table(connection, 'canon_task_edges'):
            raise LaneError('CANON_EDGE_NOT_FOUND', 'Select an edge recorded in this project.')
        row = connection.execute('SELECT * FROM canon_task_edges WHERE edge_id=?', (edge_id,)).fetchone()
        if row is None:
            raise LaneError('CANON_EDGE_NOT_FOUND', 'Select an edge recorded in this project.')
        body = json.loads(row['body_json'])
        definition = body.get('definition', {})
        source, destination = body.get('source', {}), definition.get('destination', {})
        if (content_digest(body) != row['edge_digest'] or definition.get('edge_id') != edge_id
                or body.get('schema') != 'evidence-lane.canon-task-edge.v4'
                or self._node_key(source) != row['source_node'] or self._node_key(destination) != row['destination_node']
                or self.store.project_id not in {source['project_id'], destination['project_id']}):
            raise LaneError('CANON_EDGE_INTEGRITY', 'The edge differs from its immutable endpoint and scope identity.')
        CanonTaskEdgeRegister.model_validate({**definition, 'source_id':source['participant_id']})
        path = self._edge_path(edge_id, row['edge_digest'])
        if row['artifact_path'] != path.relative_to(self.store.root).as_posix():
            raise LaneError('CANON_EDGE_FILE_INTEGRITY', 'The graph file reference differs from its lane-owned location.')
        expected = json_text(body).encode()
        try:
            if path.stat().st_size != len(expected) or path.read_bytes() != expected:
                raise LaneError('CANON_EDGE_FILE_INTEGRITY', 'The named graph file differs from its registered bytes.')
        except OSError:
            raise LaneError('CANON_EDGE_FILE_INTEGRITY', 'The registered graph file is unavailable.') from None
        # Endpoint ownership at sealing is historical evidence. A later
        # continuation may legitimately change the current participant owner.
        binding = connection.execute('SELECT e.* FROM canon_task_edge_bindings b JOIN canon_events e USING(request_id) WHERE b.edge_id=?', (edge_id,)).fetchone()
        if binding:
            event = dict(binding)
            digest = event.pop('digest')
            result = json.loads(binding['result_json'])
            if (content_digest(event) != digest or binding['kind'] != 'task_edge_bind'
                    or result.get('edge_id') != edge_id or result.get('edge_digest') != row['edge_digest']
                    or result.get('destination_bound') is not True):
                raise LaneError('CANON_EDGE_BINDING_INTEGRITY', 'The destination binding differs from its attributed event.')
        if source['project_id'] == self.store.project_id:
            role = 'both' if binding else 'source'
        else:
            if binding is None:
                raise LaneError('CANON_EDGE_BINDING_INTEGRITY', 'An imported edge lacks its destination binding.')
            role = 'destination'
        return CanonTaskEdgeResult(project_id=self.store.project_id, edge_id=edge_id,
            edge_digest=row['edge_digest'], edge=body, artifact_path=row['artifact_path'],
            local_role=role, destination_bound=binding is not None)

    def _graph_edges(self, connection, *, limit=5000):
        if not self._table(connection, 'canon_task_edges'):
            return []
        rows = connection.execute('SELECT edge_id FROM canon_task_edges ORDER BY sequence LIMIT ?', (limit + 1,)).fetchall()
        if len(rows) > limit:
            raise LaneError('CANON_GRAPH_BUDGET', 'The full recorded graph exceeds the selected verification budget.')
        edges, pairs = [], []
        for row in rows:
            edge = self._edge(connection, row['edge_id'])
            source = self._node_key(edge.edge['source'])
            destination = self._node_key(edge.edge['definition']['destination'])
            if self._would_cycle(pairs, source, destination):
                raise LaneError('CANON_TASK_GRAPH_CYCLE_BLOCKED', 'The recorded task graph contains a cycle.')
            pairs.append((source, destination))
            edges.append(edge)
        return edges

    def _write_edge(self, connection, body, lease, *, require_local_dependencies=True):
        edge_id = body['definition']['edge_id']
        digest = content_digest(body)
        existing = connection.execute('SELECT edge_digest FROM canon_task_edges WHERE edge_id=?', (edge_id,)).fetchone()
        if existing:
            if existing[0] != digest:
                raise LaneError('CANON_EDGE_IMMUTABILITY_CONFLICT', 'This edge ID already belongs to another immutable definition.')
            return
        edges = self._graph_edges(connection)
        pairs = [(self._node_key(edge.edge['source']), self._node_key(edge.edge['definition']['destination'])) for edge in edges]
        source, destination = self._node_key(body['source']), self._node_key(body['definition']['destination'])
        if self._would_cycle(pairs, source, destination):
            raise LaneError('CANON_TASK_GRAPH_CYCLE_BLOCKED', 'The proposed edge would create a task-routing cycle.')
        if require_local_dependencies and not set(body['definition']['dependency_ids']) <= {edge.edge_id for edge in edges}:
            raise LaneError('CANON_EDGE_DEPENDENCY_NOT_FOUND', 'Record the exact dependency edges in this project before linking them.')
        path = self._edge_path(edge_id, digest)
        content = json_text(body).encode()
        lease.check()
        path.parent.mkdir(parents=True, exist_ok=True)
        reject_links(path, self.store.root)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if path.stat().st_size != len(content) or path.read_bytes() != content:
                raise LaneError('CANON_EDGE_FILE_INTEGRITY', 'Preserve the existing graph file; its bytes differ from this edge.') from None
        else:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        if path.stat().st_size != len(content) or path.read_bytes() != content:
            raise LaneError('CANON_EDGE_FILE_INTEGRITY', 'The named graph file failed write verification.')
        # Only committed rows expose a graph file. Aborted writes can leave
        # unregistered immutable files, excluded from reads and recovery sets.
        connection.execute('INSERT INTO canon_task_edges(edge_id,edge_digest,source_node,destination_node,body_json,artifact_path) VALUES(?,?,?,?,?,?)',
            (edge_id, digest, source, destination, json_text(body), path.relative_to(self.store.root).as_posix()))

    def register_task_edge(self, request, lease, *, actor_id, destination_participant=None):
        self.initialize(lease)
        with lease.transaction('canon') as connection:
            key = content_digest(request.model_dump())
            self.participant(connection, request.source_id, owner=actor_id)
            prior = self._replay(connection, request.request_id, 'task_edge_register', actor_id, key)
            if prior:
                return self._edge(connection, request.edge_id).model_copy(update={'duplicate':True})
            self._expiry(request.model_dump())
            source = self.participant(connection, request.source_id, owner=actor_id)
            destination = (self.participant(connection, request.destination.participant_id)
                if request.destination.project_id == self.store.project_id else destination_participant)
            if destination is None or (destination['project_id'], destination['participant_id']) != (request.destination.project_id, request.destination.participant_id):
                raise LaneError('CANON_EDGE_DESTINATION_UNVERIFIED', 'Read the exact destination participant through its authorized project selection.')
            body = {'schema':'evidence-lane.canon-task-edge.v4', 'source':{'project_id':self.store.project_id,'participant_id':request.source_id},
                'definition':request.model_dump(exclude={'request_id','source_id'}), 'source_at_seal':source,
                'destination_at_seal':destination, 'created_at':now(), 'one_writer':True,
                'approval_propagation_allowed':False, 'pointer_propagation_allowed':False,
                'source_write_granted':False, 'native_task_attestation':'not_provided'}
            self._write_edge(connection, body, lease)
            result = self._edge(connection, request.edge_id)
            self._event(connection, request.request_id, 'task_edge_register', actor_id, key, result.model_dump())
            self.store.append_receipt('canon_task_edge_registered', result.model_dump(), connection=connection)
            return result

    def bind_task_edge(self, request, lease, *, actor_id, source_edge=None):
        self.initialize(lease)
        with lease.transaction('canon') as connection:
            source = self._edge(connection, request.edge_id) if request.source_project_id == self.store.project_id else source_edge
            if (source is None or source.edge_digest != request.edge_digest or source.edge_id != request.edge_id
                    or source.edge['source']['project_id'] != request.source_project_id):
                raise LaneError('CANON_EDGE_SOURCE_MISMATCH', 'The selected source project does not contain this exact immutable edge.')
            destination = source.edge['definition']['destination']
            if destination['project_id'] != self.store.project_id:
                raise LaneError('CANON_EDGE_DESTINATION_PROJECT_MISMATCH', 'Only the exact destination project may bind this edge.')
            self.participant(connection, destination['participant_id'], owner=actor_id)
            key = content_digest(request.model_dump())
            prior = self._replay(connection, request.request_id, 'task_edge_bind', actor_id, key)
            if prior:
                return self._edge(connection, request.edge_id).model_copy(update={'duplicate':True})
            self._expiry(source.edge['definition'])
            existing = connection.execute('SELECT request_id FROM canon_task_edge_bindings WHERE edge_id=?', (request.edge_id,)).fetchone()
            if existing:
                return self._edge(connection, request.edge_id).model_copy(update={'duplicate':True})
            # The source verified its own dependency locators when sealing.
            # A destination does not acquire those other projects' edges.
            self._write_edge(connection, source.edge, lease, require_local_dependencies=False)
            path = self._edge_path(request.edge_id, request.edge_digest)
            result = CanonTaskEdgeResult(project_id=self.store.project_id, edge_id=request.edge_id, edge_digest=request.edge_digest,
                edge=source.edge, artifact_path=path.relative_to(self.store.root).as_posix(),
                local_role='both' if request.source_project_id == self.store.project_id else 'destination', destination_bound=True)
            self._event(connection, request.request_id, 'task_edge_bind', actor_id, key, result.model_dump())
            connection.execute('INSERT INTO canon_task_edge_bindings VALUES(?,?)', (request.edge_id, request.request_id))
            self.store.append_receipt('canon_task_edge_bound', result.model_dump(), connection=connection)
            return self._edge(connection, request.edge_id)

    def task_graph(self, request):
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            history = self._verify_history(connection, limit=request.record_limit)
            edges = self._graph_edges(connection, limit=request.record_limit)
            returns = {}
            if self._table(connection, 'canon_exchanges'):
                rows = connection.execute("SELECT exchange_id FROM canon_exchanges WHERE kind='result' AND state='admitted' ORDER BY sequence LIMIT ?", (request.record_limit + 1,)).fetchall()
                if len(rows) > request.record_limit:
                    raise LaneError('CANON_GRAPH_BUDGET', 'The recorded returns exceed the selected verification budget.')
                for row in rows:
                    result, body = self.exchange(connection, row['exchange_id'])
                    message = body['message']
                    if message.get('edge_id'):
                        self._validate_edge_message(connection, CanonSend.model_validate(message), historical=True, source_project_id=body['project_id'])
                        returns.setdefault(message['edge_id'], []).append({'exchange_id':result['exchange_id'], 'envelope_digest':result['envelope_digest']})
            nodes, values, missing = set(), [], []
            edge_ids = {item.edge_id for item in edges}
            for edge in edges:
                definition = edge.edge['definition']
                nodes.update((self._node_key(edge.edge['source']), self._node_key(definition['destination'])))
                observed = returns.get(edge.edge_id, [])
                value = {**edge.model_dump(), 'local_admitted_returns':observed,
                    'return_observation_scope':'selected_project_exchange_history',
                    'dependencies_not_recorded_locally':[identity for identity in definition['dependency_ids']
                        if identity not in edge_ids]}
                values.append(value)
                if not observed:
                    missing.append({'edge_id':edge.edge_id, 'destination':definition['destination'],
                        'expected_return_contract':definition['expected_return_contract'],
                        'state':'missing_local_admitted_return' if edge.edge['source']['project_id'] == self.store.project_id else 'source_return_state_not_read'})
            result = CanonTaskGraph(project_id=self.store.project_id, nodes=sorted(nodes), edges=values,
                missing_returns=missing, history=history, graph_digest='')
            result = result.model_copy(update={'graph_digest':content_digest(result.model_dump(exclude={'graph_digest'}))})
            if len(result.model_dump_json().encode()) > request.max_bytes:
                raise LaneError('CANON_GRAPH_OUTPUT_BUDGET', 'The verified graph exceeds the selected response byte budget.')
            return result

    def _validate_edge_message(self, connection, request, *, historical=False, source_project_id=None):
        if not request.edge_id:
            return
        edge = self._edge(connection, request.edge_id)
        definition = edge.edge['definition']
        source, destination = edge.edge['source'], definition['destination']
        # The receiver owns the binding. A source-only copy cannot invent its
        # peer's current binding state; admission and returns check it there.
        if destination['project_id'] == self.store.project_id and not edge.destination_bound:
            raise LaneError('CANON_EDGE_NOT_BOUND', 'The destination participant must bind this edge before admitting input or returning results.')
        if not historical:
            self._expiry(definition)
        if definition['schema_digest'] != content_digest(CanonPayload.model_json_schema()):
            raise LaneError('CANON_EDGE_SCHEMA_MISMATCH', 'The edge does not select the current typed task exchange authority payload schema.')
        returning = request.kind in {'result', 'clarification', 'backfire'}
        route = (destination, source) if returning else (source, destination)
        sender_project = source_project_id or self.store.project_id
        actual = ({'project_id':sender_project, 'participant_id':request.sender_id},
            {'project_id':request.destination_project_id or sender_project, 'participant_id':request.receiver_id})
        contract = definition['expected_return_contract'] if returning else definition['contract_digest']
        if actual != route or request.expected_contract != contract:
            raise LaneError('CANON_EDGE_CONTRACT_MISMATCH', 'The packet must use this exact edge route and pinned input or return contract.')
        if returning:
            _, parent = self.exchange(connection, request.reply_to)
            if (parent['message'].get('edge_id') != request.edge_id or parent['message'].get('return_contract') != contract
                    or self._route(parent) != (source['project_id'], destination['project_id'])
                    or (parent['message']['sender_id'], parent['message']['receiver_id']) != (source['participant_id'], destination['participant_id'])):
                raise LaneError('CANON_EDGE_RETURN_MISMATCH', 'The return must address input from this exact edge and its pinned return contract.')
        elif request.return_contract != definition['expected_return_contract']:
            raise LaneError('CANON_EDGE_RETURN_MISMATCH', 'The edge input must pin its exact expected return contract.')

    def initialize(self,lease):
        if lease.store.root!=self.store.root or lease.store.project_id!=self.store.project_id:
            raise LaneError('WRITER_PROJECT_MISMATCH','The task exchange authority writer belongs to another project.')
        lease.check()
        apply_migrations(self.store,CANON_MIGRATIONS,writer=lease)

    @staticmethod
    def _table(connection,name):
        return connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?",(name,)).fetchone() is not None

    @staticmethod
    def _event(connection,request_id,kind,actor_id,input_digest,result):
        previous=connection.execute('SELECT sequence,digest FROM canon_events ORDER BY sequence DESC LIMIT 1').fetchone()
        body={'sequence':previous['sequence']+1 if previous else 1,'request_id':request_id,'kind':kind,'actor_id':actor_id,
              'input_digest':input_digest,'result_json':json_text(result),'previous_digest':previous['digest'] if previous else None,'created_at':now()}
        digest=content_digest(body)
        connection.execute('INSERT INTO canon_events VALUES(?,?,?,?,?,?,?,?,?)',(body['sequence'],request_id,kind,actor_id,input_digest,body['result_json'],body['previous_digest'],digest,body['created_at']))
        return digest

    @staticmethod
    def _replay(connection,request_id,kind,actor_id,input_digest):
        row=connection.execute('SELECT * FROM canon_events WHERE request_id=?',(request_id,)).fetchone()
        if row:
            if (row['kind'],row['actor_id'],row['input_digest'])!=(kind,actor_id,input_digest):
                raise LaneError('CANON_REQUEST_CONFLICT','This request ID belongs to different content or ownership.')
            value=dict(row)
            digest=value.pop('digest')
            if content_digest(value)!=digest:
                raise LaneError('CANON_HISTORY_INTEGRITY','The task exchange authority event differs from its identity.')
            return json.loads(row['result_json'])
        return None

    def participant(self,connection,participant_id,*,owner=None):
        if not self._table(connection,'canon_participants'):
            raise LaneError('CANON_PARTICIPANT_NOT_FOUND','Select an existing participant in this project.')
        row=connection.execute('SELECT * FROM canon_participants WHERE participant_id=?',(participant_id,)).fetchone()
        if not row:
            raise LaneError('CANON_PARTICIPANT_NOT_FOUND','Select an existing participant in this project.')
        body=json.loads(row['body_json'])
        if (content_digest(body)!=row['digest'] or body.get('project_id')!=self.store.project_id
            or body.get('participant_id')!=participant_id or body.get('owner_client_id')!=row['owner_client_id']):
            raise LaneError('CANON_PARTICIPANT_INTEGRITY','The participant ownership record is inconsistent.')
        if owner is not None and row['owner_client_id']!=owner:
            raise LaneError('CANON_OWNER_MISMATCH','Only the owning engine client can act for this participant.')
        return body

    def join(self,request,lease,*,actor_id):
        self.initialize(lease)
        with lease.transaction('canon') as connection:
            key=content_digest(request.model_dump())
            prior=self._replay(connection,request.request_id,'join',actor_id,key)
            if prior:return CanonParticipant(**prior)
            result=CanonParticipant(project_id=self.store.project_id,participant_id=str(uuid4()),owner_client_id=actor_id,
                                    label=request.label,reported_host_task_id=request.reported_host_task_id)
            body=result.model_dump()
            connection.execute('INSERT INTO canon_participants VALUES(?,?,?,?,?)',(result.participant_id,actor_id,json_text(body),content_digest(body),now()))
            self._event(connection,request.request_id,'join',actor_id,key,body)
            return result

    def expect(self,request,lease,*,actor_id,foreign_participants=()):
        self.initialize(lease)
        with lease.transaction('canon') as connection:
            key=content_digest(request.model_dump())
            prior=self._replay(connection,request.request_id,'expect',actor_id,key)
            if prior:return CanonContractResult(**prior)
            self.participant(connection,request.receiver_id,owner=actor_id)
            for sender in request.sender_ids:self.participant(connection,sender)
            foreign = {(p['project_id'], p['participant_id']): p for p in foreign_participants}
            for endpoint in request.sender_endpoints:
                if endpoint.project_id == self.store.project_id:
                    self.participant(connection, endpoint.participant_id)
                    if endpoint.participant_id in request.sender_ids:
                        raise LaneError('CANON_DUPLICATE_ENDPOINT', 'Declare each source endpoint once.')
                elif (endpoint.project_id, endpoint.participant_id) not in foreign:
                    raise LaneError('CANON_FOREIGN_SOURCE_NOT_VERIFIED', 'Verify the exact permitted foreign participant through an authorized project read.')
            current=connection.execute('SELECT v.* FROM canon_contract_current c JOIN canon_contracts v USING(contract_digest) WHERE c.receiver_id=? AND c.contract_key=?',
                                       (request.receiver_id,request.contract_key)).fetchone()
            version=current['version'] if current else 0
            if version!=request.expected_version:
                raise LaneError('CANON_CONTRACT_VERSION_CONFLICT','Refresh the exact current receiver contract before replacing it.')
            body={'project_id':self.store.project_id,'contract':request.model_dump(exclude={'request_id','expected_version'}),
                  'version':version+1,'source_client_id':actor_id,'payload_schema_digest':content_digest(CanonPayload.model_json_schema()),'created_at':now()}
            digest=content_digest(body)
            connection.execute('INSERT INTO canon_contracts VALUES(?,?,?,?,?)',(digest,request.receiver_id,request.contract_key,version+1,json_text(body)))
            connection.execute('INSERT INTO canon_contract_current VALUES(?,?,?) ON CONFLICT(receiver_id,contract_key) DO UPDATE SET contract_digest=excluded.contract_digest',
                               (request.receiver_id,request.contract_key,digest))
            result=CanonContractResult(contract_digest=digest,version=version+1,contract=body)
            self._event(connection,request.request_id,'expect',actor_id,key,result.model_dump())
            return result

    def _contract(self,connection,digest):
        if not self._table(connection,'canon_contracts'):
            raise LaneError('CANON_CONTRACT_NOT_FOUND','Select an expected-input contract from this project.')
        row=connection.execute('SELECT * FROM canon_contracts WHERE contract_digest=?',(digest,)).fetchone()
        if not row:raise LaneError('CANON_CONTRACT_NOT_FOUND','Select an expected-input contract from this project.')
        body=json.loads(row['body_json'])
        if (content_digest(body)!=digest or body.get('project_id')!=self.store.project_id or body['version']!=row['version']
            or body['contract']['receiver_id']!=row['receiver_id'] or body['contract']['contract_key']!=row['contract_key']):
            raise LaneError('CANON_CONTRACT_INTEGRITY','The expected-input contract differs from its identity.')
        return body

    def _sender_allowed(self, contract, participant_id, project_id):
        return (project_id == self.store.project_id and participant_id in contract['sender_ids']
            or {'project_id':project_id, 'participant_id':participant_id} in contract.get('sender_endpoints', []))

    def _compatibility(self, connection, request, *, source_project_id=None):
        if not request.expected_contract:
            return False, ['CANON_EXPECTED_CONTRACT_UNDEFINED']
        try:
            return self._match(connection, request, source_project_id=source_project_id), []
        except LaneError as error:
            if error.code not in {'CANON_CONTRACT_NOT_FOUND', 'CANON_CONTRACT_MISMATCH', 'CANON_PAYLOAD_TYPE_MISMATCH'}:
                raise
            return False, [error.code]

    def _match(self,connection,request,*,source_project_id=None):
        if not request.expected_contract:return False
        body=self._contract(connection,request.expected_contract)
        contract=body['contract']
        current=connection.execute('SELECT contract_digest FROM canon_contract_current WHERE receiver_id=? AND contract_key=?',
                                   (request.receiver_id,contract['contract_key'])).fetchone()
        if (not current or current[0]!=request.expected_contract or not contract['active'] or contract['receiver_id']!=request.receiver_id
                or body['payload_schema_digest']!=content_digest(CanonPayload.model_json_schema())
                or not self._sender_allowed(contract, request.sender_id, source_project_id or self.store.project_id) or request.kind not in contract['kinds']
                or len(request.payload.model_dump_json().encode())>contract['max_bytes']
                or not set(contract['required_reference_kinds'])<={ref.kind for ref in request.payload.references}):
            raise LaneError('CANON_CONTRACT_MISMATCH','The message does not match the exact current receiver contract.')
        types={'string':lambda x:isinstance(x,str),'integer':lambda x:type(x) is int,'number':lambda x:type(x) in {int,float},
               'boolean':lambda x:type(x) is bool,'null':lambda x:x is None}
        if (set(request.payload.fields)!=set(contract['fields'])
                or any(not types[kind](request.payload.fields[name]) for name,kind in contract['fields'].items())):
            raise LaneError('CANON_PAYLOAD_TYPE_MISMATCH','The named payload fields differ from the receiver schema.')
        return contract['auto_admit']

    def _route(self, body):
        source = body['project_id']
        destination = body['message'].get('destination_project_id') or source
        return source, destination

    def _role(self, source, destination):
        if source == destination == self.store.project_id:
            return 'both'
        if source == self.store.project_id:
            return 'outbox'
        if destination == self.store.project_id:
            return 'inbox'
        raise LaneError('CANON_PROJECT_ROUTE_MISMATCH', 'This project is not an endpoint of the exact packet.')

    def _packet_path(self, body, digest, role):
        if (not re.fullmatch(UUID_PATTERN, body.get('exchange_id','')) or not re.fullmatch(DIGEST, digest)
                or role not in {'outbox','inbox','both'}):
            raise LaneError('CANON_PACKET_FILE_INTEGRITY', 'Packet files require exact bounded UUID and digest locators.')
        folder = 'outbox' if role == 'outbox' else 'inbox'
        return f"authorities/canon/files/{folder}/{digest}/{body['exchange_id']}.json"

    def _packet_file(self, body, digest, role, *, write=False):
        relative = self._packet_path(body, digest, role)
        path = self.project.root / relative
        reject_links(path, self.project.root)
        content = json_text(body).encode()
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            reject_links(path, self.project.root)
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
        if not path.is_file() or path.stat().st_size != len(content) or path.read_bytes() != content:
            raise LaneError('CANON_PACKET_FILE_INTEGRITY', 'The registered immutable packet file is missing or changed.')
        return relative

    def exchange(self,connection,identity):
        if not self._table(connection,'canon_exchanges'):
            raise LaneError('CANON_EXCHANGE_NOT_FOUND','The exchange is not recorded in this project.')
        row=connection.execute('SELECT * FROM canon_exchanges WHERE exchange_id=?',(identity,)).fetchone()
        if row is None:raise LaneError('CANON_EXCHANGE_NOT_FOUND','The exchange is not recorded in this project.')
        body=json.loads(row['body_json'])
        source, destination = self._route(body)
        role = self._role(source, destination)
        if (content_digest(body)!=row['envelope_digest']
            or body.get('exchange_id')!=identity or (body['message']['sender_id'],body['message']['receiver_id'],body['message']['kind'])
               !=(row['sender_id'],row['receiver_id'],row['kind'])
            or (row['source_project_id'], row['destination_project_id'], row['local_role']) != (source, destination, role)):
            raise LaneError('CANON_ENVELOPE_INTEGRITY','The exchange differs from its immutable envelope and project endpoints.')
        if row['state_digest']!=self._state_digest(identity,row['envelope_digest'],row['state'],row['version']):
            raise LaneError('CANON_STATE_INTEGRITY','The exchange state differs from its recorded version.')
        if (role == 'outbox') != (row['state'] == 'sealed'):
            raise LaneError('CANON_STATE_INTEGRITY', 'An outbound seal cannot claim a receiver decision.')
        if source == self.store.project_id:
            self.participant(connection, row['sender_id'])
        if destination == self.store.project_id:
            self.participant(connection, row['receiver_id'])
        if body.get('schema') == 'evidence-lane.canon-envelope.v4':
            CanonSend.model_validate(body['message'])
            if row['artifact_path'] != self._packet_file(body, row['envelope_digest'], role):
                raise LaneError('CANON_PACKET_FILE_INTEGRITY', 'The packet locator differs from its exact lane file.')
        elif role != 'both' or row['artifact_path'] is not None:
            raise LaneError('CANON_ENVELOPE_INTEGRITY', 'Historical envelopes only describe their original local exchange.')
        return row,body

    def _insert_exchange(self, connection, body, state):
        source, destination = self._route(body)
        role = self._role(source, destination)
        identity, message = body['exchange_id'], body['message']
        digest = content_digest(body)
        relative = self._packet_file(body, digest, role, write=True)
        connection.execute("""INSERT INTO canon_exchanges(exchange_id,sender_id,receiver_id,kind,envelope_digest,body_json,state,version,
            state_digest,parent_id,created_at,source_project_id,destination_project_id,local_role,artifact_path)
            VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)""", (identity,message['sender_id'],message['receiver_id'],message['kind'],digest,
                json_text(body),state,self._state_digest(identity,digest,state,1),
                message['reply_to'] if message['reply_to'] and connection.execute(
                    'SELECT 1 FROM canon_exchanges WHERE exchange_id=?',(message['reply_to'],)).fetchone() else None,
                body['created_at'],source,destination,role,relative))
        return CanonSent(exchange_id=identity,envelope_digest=digest,state=state,local_role=role,artifact_path=relative)

    def _admission_evidence(self, connection, identity):
        event = connection.execute("SELECT * FROM canon_events WHERE kind IN ('send','receive') "
            "AND json_extract(result_json,'$.exchange_id')=? ORDER BY sequence LIMIT 1",(identity,)).fetchone()
        if event:
            fields = dict(event)
            digest = fields.pop('digest')
            if content_digest(fields) != digest:
                raise LaneError('CANON_HISTORY_INTEGRITY', 'The initial admission evidence differs from its immutable event.')
        value = json.loads(event['result_json']) if event else {}
        return {key:value[key] for key in ('compatibility_reasons','receiver_decision_required') if key in value}

    def _delivery_result(self, connection, row, *, duplicate=False):
        evidence = self._admission_evidence(connection, row['exchange_id'])
        return CanonSent(exchange_id=row['exchange_id'],envelope_digest=row['envelope_digest'],state=row['state'],
            local_role=row['local_role'],artifact_path=row['artifact_path'],duplicate=duplicate,
            compatibility_reasons=evidence.get('compatibility_reasons',[]),receiver_decision_required=row['state'] in {'received','needs_clarification'})

    def source_snapshot(self, connection, request, *, preview=False):
        row, body = self.exchange(connection, request.exchange_id)
        if row['envelope_digest'] != request.envelope_digest or row['local_role'] != 'outbox':
            raise LaneError('CANON_SOURCE_ENVELOPE_MISMATCH', 'Read the exact immutable outbox packet from its source project.')
        event = connection.execute("SELECT * FROM canon_events WHERE kind='send' "
            "AND json_extract(result_json,'$.exchange_id')=? ORDER BY sequence LIMIT 1",(request.exchange_id,)).fetchone()
        if event is None:
            raise LaneError('CANON_SOURCE_SEAL_INTEGRITY', 'The source envelope lacks its exact attributable sealing event.')
        fields = dict(event)
        event_digest = fields.pop('digest')
        sealed = json.loads(event['result_json'])
        if (content_digest(fields) != event_digest or sealed.get('envelope_digest') != row['envelope_digest']
                or sealed.get('state') != 'sealed' or sealed.get('local_role') != 'outbox'
                or event['actor_id'] != body['source_client_id']
                or event['input_digest'] != content_digest({**body['message'],'request_id':event['request_id']})):
            raise LaneError('CANON_SOURCE_SEAL_INTEGRITY', 'The source envelope, actor and input must match their original sealing event.')
        source_seal = {key:event[key] for key in ('request_id','actor_id','input_digest','digest')}
        message = CanonSend.model_validate(body['message'])
        if message.backfire:
            operation = connection.execute("SELECT * FROM canon_events WHERE kind='backfire' "
                "AND json_extract(result_json,'$.exchange_id')=?",(request.exchange_id,)).fetchone()
            if operation is None:
                raise LaneError('CANON_OPERATION_INTEGRITY', 'A typed backfire requires its source-owned conditional operation record.')
            self._operation_record(connection, operation)
        source_reasons = []
        backfire_observation = None
        try:
            self._expiry(body['message'])
            for ref in message.payload.references:
                ProjectMemory(self.store)._reference(connection, ref)
            if message.backfire:
                backfire_observation = self._validate_backfire_source(connection, message)
        except LaneError as error:
            if not preview or error.code not in {'CANON_EXCHANGE_EXPIRED','MEMORY_SOURCE_MISMATCH',
                    'CANON_BACKFIRE_REQUIRES_ADMITTED_INPUT','CANON_BACKFIRE_STALE_REVISION_BLOCKED'}:
                raise
            source_reasons.append(error.code)
        parent_observation = None
        parent_envelope = None
        if message.reply_to:
            parent, parent_envelope = self.exchange(connection, message.reply_to)
            parent_observation = {key:parent[key] for key in ('exchange_id','envelope_digest','state','version')}
        return {'envelope':body, 'envelope_digest':row['envelope_digest'], 'parent_observation':parent_observation,
            'parent_envelope':parent_envelope,'backfire_observation':backfire_observation,
            'source_project_id':self.store.project_id, 'source_history':self._verify_history(connection), 'source_reasons':source_reasons, 'source_seal':source_seal}

    @staticmethod
    def _state_digest(identity,envelope_digest,state,version):
        return content_digest({'exchange_id':identity,'envelope_digest':envelope_digest,'state':state,'version':version})

    @staticmethod
    def _expiry(message):
        expires=message.get('expires_at')
        if expires and datetime.fromisoformat(expires)<=datetime.now(UTC):
            raise LaneError('CANON_EXCHANGE_EXPIRED','This exchange has expired; request a current replacement.')

    def _supersession(self, connection, row):
        event = connection.execute('SELECT e.* FROM canon_supersessions s JOIN canon_events e USING(request_id) '
            'WHERE s.exchange_id=?',
            (row['exchange_id'],)).fetchone()
        if event is None:
            return None  # Do not invent a missing link for historical prototype rows.
        value = dict(event)
        digest = value.pop('digest')
        if content_digest(value) != digest or event['kind'] != 'supersede':
            raise LaneError('CANON_HISTORY_INTEGRITY', 'The recorded supersession event differs from its identity.')
        result = CanonSuperseded.model_validate_json(event['result_json'])
        successor, _ = self.exchange(connection, result.successor_id)
        if (result.exchange_id != row['exchange_id'] or row['state'] != 'superseded' or result.envelope_digest != row['envelope_digest']
                or result.version != row['version'] or result.receiver_id != row['receiver_id']
                or successor['envelope_digest'] != result.successor_envelope_digest
                or successor['sequence'] <= row['sequence']
                or (successor['sender_id'], successor['receiver_id'], successor['source_project_id'], successor['destination_project_id'])
                   != (row['sender_id'], row['receiver_id'], row['source_project_id'], row['destination_project_id'])):
            raise LaneError('CANON_SUPERSESSION_INTEGRITY', 'The immutable successor link differs from its exchange route or state.')
        return result

    def _supersede(self, connection, original_id, successor_id, *, actor_id, basis, request_id=None, input_digest=None):
        row, _ = self.exchange(connection, original_id)
        successor, successor_body = self.exchange(connection, successor_id)
        if ((row['sender_id'], row['receiver_id'], row['source_project_id'], row['destination_project_id'])
                != (successor['sender_id'], successor['receiver_id'], successor['source_project_id'], successor['destination_project_id'])
                or successor['sequence'] <= row['sequence']):
            raise LaneError('CANON_SUPERSESSION_LINEAGE_INVALID', 'Supersession requires a newer exchange on the exact same route.')
        if row['state'] == 'superseded':
            previous = self._supersession(connection, row)
            if previous is None:
                raise LaneError('CANON_SUPERSESSION_RECORD_UNAVAILABLE', 'This historical superseded exchange has no recorded successor link.')
            if previous.successor_id != successor_id:
                raise LaneError('CANON_SUPERSESSION_REPLAY_CONFLICT', 'The exchange was already superseded by a different successor.')
            return previous.model_copy(update={'duplicate': True})
        if row['state'] not in {'admitted', 'rejected', 'needs_clarification'}:
            raise LaneError('CANON_SUPERSESSION_STATE_INVALID', 'Decide the original input before superseding its revision.')
        if successor['state'] != 'admitted':
            raise LaneError('CANON_SUPERSESSION_SUCCESSOR_NOT_ADMITTED', 'The receiver must admit the successor before it replaces existing input.')
        self._expiry(successor_body['message'])
        result = CanonSuperseded(exchange_id=original_id, envelope_digest=row['envelope_digest'],
            successor_id=successor_id, successor_envelope_digest=successor['envelope_digest'],
            receiver_id=row['receiver_id'], version=row['version'] + 1, decision_basis=basis)
        connection.execute("UPDATE canon_exchanges SET state='superseded',version=?,state_digest=? WHERE exchange_id=?",
            (result.version, self._state_digest(original_id, row['envelope_digest'], 'superseded', result.version), original_id))
        event_id = request_id or str(uuid4())
        self._event(connection, event_id, 'supersede', actor_id,
            input_digest or content_digest(result.model_dump()), result.model_dump())
        connection.execute('INSERT INTO canon_supersessions VALUES(?,?)', (original_id, event_id))
        self.store.append_receipt('canon_superseded', result.model_dump(), connection=connection)
        return result

    def supersede(self, request, lease, *, actor_id):
        self.initialize(lease)
        with lease.transaction('canon') as connection:
            key = content_digest(request.model_dump())
            prior = self._replay(connection, request.request_id, 'supersede', actor_id, key)
            if prior:
                row, _ = self.exchange(connection, request.exchange_id)
                self.participant(connection, row['receiver_id'], owner=actor_id)
                current = self._supersession(connection, row)
                if current is None or current.model_dump() != prior:
                    raise LaneError('CANON_SUPERSESSION_INTEGRITY', 'The replay differs from the recorded successor link.')
                return current.model_copy(update={'duplicate': True})
            row, _ = self.exchange(connection, request.exchange_id)
            successor, _ = self.exchange(connection, request.successor_id)
            self.participant(connection, row['receiver_id'], owner=actor_id)
            if (row['envelope_digest'] != request.envelope_digest or row['version'] != request.expected_version
                    or successor['envelope_digest'] != request.successor_envelope_digest):
                raise LaneError('CANON_SUPERSESSION_VERSION_CONFLICT', 'Select both exact envelopes and the current original state version.')
            return self._supersede(connection, request.exchange_id, request.successor_id,
                actor_id=actor_id, basis='receiver_explicit', request_id=request.request_id, input_digest=key)

    def _validate_return(self, connection, request, *, source_project_id, destination_project_id, source_observation=None):
        depth = 0
        if request.reply_to:
            observation = (source_observation or {}).get('parent_observation') or {}
            try:
                parent, body = self.exchange(connection, request.reply_to)
            except LaneError as error:
                body = (source_observation or {}).get('parent_envelope') or {}
                if (error.code != 'CANON_EXCHANGE_NOT_FOUND' or not body.get('message',{}).get('backfire')
                        or body.get('exchange_id') != request.reply_to or content_digest(body) != observation.get('envelope_digest')
                        or observation.get('exchange_id') != request.reply_to):
                    raise
                parent = {**observation,'local_role':'source_observation'}
            parent_message = CanonSend.model_validate(body['message'])
            self._expiry(body['message'])
            if body['message'].get('return_contract') and request.expected_contract != body['message']['return_contract']:
                raise LaneError('CANON_RETURN_CONTRACT_MISMATCH', 'This return must use the contract pinned by its source exchange.')
            parent_source, parent_destination = self._route(body)
            return_route = parent_message.backfire.return_route if parent_message.backfire else CanonTaskEndpoint(
                project_id=parent_source, participant_id=parent_message.sender_id)
            if ((parent_destination,parent_message.receiver_id) != (source_project_id,request.sender_id)
                    or (return_route.project_id,return_route.participant_id) != (destination_project_id,request.receiver_id)):
                raise LaneError('CANON_RETURN_ROUTE_MISMATCH', 'A return must follow the exact earlier project and participant return route.')
            if parent['local_role'] == 'outbox':
                if (observation.get('exchange_id') != parent['exchange_id'] or observation.get('envelope_digest') != parent['envelope_digest']
                        or observation.get('state') not in {'admitted','needs_clarification'}):
                    raise LaneError('CANON_RETURN_REQUIRES_INPUT', 'Verify the returning project admitted or requested clarification on this exact input.')
            elif parent['state'] not in {'admitted','needs_clarification'}:
                raise LaneError('CANON_RETURN_REQUIRES_INPUT', 'Admit or request clarification on the earlier input first.')
            depth = body.get('return_depth', 0) + 1
            if depth > 8:
                raise LaneError('CANON_RETURN_DEPTH', 'Stop this exchange chain and review its unresolved input.')
        if request.supersedes:
            old, body = self.exchange(connection, request.supersedes)
            if ((old['sender_id'], old['receiver_id']) != (request.sender_id, request.receiver_id)
                    or self._route(body) != (source_project_id, destination_project_id) or old['state'] == 'superseded'):
                raise LaneError('CANON_CORRECTION_MISMATCH', 'A correction must name the current exchange on this exact project route.')
        return depth

    def _return_contract_snapshot(self, connection, digest):
        body = self._contract(connection, digest)
        expected = body['contract']
        current = connection.execute('SELECT contract_digest FROM canon_contract_current WHERE receiver_id=? AND contract_key=?',
            (expected['receiver_id'],expected['contract_key'])).fetchone()
        return {'body':body,'current_digest':current[0] if current else None}

    def _validate_backfire_source(self, connection, message):
        details = message.backfire
        row, body = self.exchange(connection, details.admitted_exchange_id)
        if row['envelope_digest'] != details.admitted_envelope_digest:
            raise LaneError('CANON_BACKFIRE_INPUT_MISMATCH', 'Select the exact immutable admitted task exchange authority input.')
        if (row['destination_project_id'] != self.store.project_id or row['receiver_id'] != message.sender_id
                or row['state'] != 'admitted' or row['local_role'] == 'outbox'):
            raise LaneError('CANON_BACKFIRE_REQUIRES_ADMITTED_INPUT', 'Only the receiving owner of a currently admitted input can raise its backfire.')
        self._expiry(body['message'])
        if details.requested_revision <= body.get('revision',1):
            raise LaneError('CANON_BACKFIRE_STALE_REVISION_BLOCKED', 'Request a newer revision than the admitted immutable packet.')
        source = CanonTaskEndpoint(project_id=self.store.project_id, participant_id=message.sender_id)
        recipient = CanonTaskEndpoint(project_id=message.destination_project_id or self.store.project_id, participant_id=message.receiver_id)
        trace = details.trace
        inherited = (body['message'].get('backfire') or {}).get('trace',[])
        if (len(trace) < 2 or trace[-2:] != [source,recipient]
                or [item.model_dump() for item in trace[:len(inherited)]] != inherited):
            raise LaneError('CANON_BACKFIRE_ROUTE_CYCLE_BLOCKED', 'Preserve the inherited correction trace and append these exact endpoints once.')
        for identity in details.dependency_ids:
            if connection.execute('SELECT 1 FROM canon_exchanges WHERE exchange_id=?',(identity,)).fetchone():
                self.exchange(connection, identity)
            else:
                self._edge(connection, identity)
        return {**{key:row[key] for key in ('exchange_id','envelope_digest','state','version')},
            'revision':body.get('revision',1),'destination':source.model_dump(),'trace':inherited}

    def _validate_message(self, connection, request, *, destination_participant=None, check_contract=True, return_contract_snapshot=None):
        """Validate locally owned source facts; only the receiver admits input."""
        destination_id = request.destination_project_id or self.store.project_id
        self.participant(connection, request.sender_id)
        if destination_id == self.store.project_id:
            self.participant(connection, request.receiver_id)
            if request.sender_id == request.receiver_id:
                raise LaneError('CANON_SELF_ROUTE', 'Select another project participant.')
        elif (not destination_participant or destination_participant['project_id'] != destination_id
                or destination_participant['participant_id'] != request.receiver_id):
            raise LaneError('CANON_REMOTE_RECEIVER_REQUIRED', 'Verify the destination participant through an authorized project read.')
        self._expiry(request.model_dump())
        self._validate_edge_message(connection, request)
        auto_admit = self._match(connection, request) if check_contract and destination_id == self.store.project_id else False
        if request.return_contract:
            route = request.backfire.return_route if request.backfire else CanonTaskEndpoint(
                project_id=self.store.project_id, participant_id=request.sender_id)
            snapshot = self._return_contract_snapshot(connection, request.return_contract) if route.project_id == self.store.project_id else return_contract_snapshot
            if not snapshot:
                raise LaneError('CANON_RETURN_CONTRACT_MISMATCH', 'Verify the exact declared return receiver contract through an authorized project read.')
            expected = snapshot['body']['contract']
            permitted = (destination_id == route.project_id and request.receiver_id in expected['sender_ids']
                or {'project_id':destination_id,'participant_id':request.receiver_id} in expected.get('sender_endpoints',[]))
            if (snapshot['body']['project_id'] != route.project_id or expected['receiver_id'] != route.participant_id
                    or not permitted or not expected['active'] or snapshot['current_digest'] != request.return_contract):
                raise LaneError('CANON_RETURN_CONTRACT_MISMATCH', 'The requested return contract must belong to the exact declared receiver and permit this recipient.')
        if request.backfire:
            self._validate_backfire_source(connection, request)
        for ref in request.payload.references:
            ProjectMemory(self.store)._reference(connection, ref)
        depth = self._validate_return(connection, request, source_project_id=self.store.project_id, destination_project_id=destination_id)
        return auto_admit, depth

    def classify(self, request):
        """Restore original pre-storage classification without inferring admission."""
        request = CanonSend.model_validate(request.model_dump())
        expected, automatic, reasons = False, False, []
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            if not self._table(connection, 'canon_participants'):
                reasons = ['CANON_PARTICIPANT_NOT_FOUND']
            else:
                try:
                    if request.kind == 'backfire':
                        raise LaneError('CANON_TYPED_BACKFIRE_REQUIRED', 'Use task_evidence_input_request to validate the conditional request before sealing.')
                    automatic, _ = self._validate_message(connection, request)
                    expected = request.expected_contract is not None
                    if not expected:
                        reasons = ['CANON_EXPECTED_CONTRACT_UNDEFINED']
                except LaneError as error:
                    # Storage or source corruption remains a failure, not a
                    # normal mismatch that a receiver could choose to admit.
                    if error.code not in {
                        'CANON_PARTICIPANT_NOT_FOUND', 'CANON_CONTRACT_NOT_FOUND',
                        'CANON_CONTRACT_MISMATCH', 'CANON_PAYLOAD_TYPE_MISMATCH',
                        'CANON_EXCHANGE_EXPIRED', 'CANON_RETURN_CONTRACT_MISMATCH',
                        'CANON_RETURN_ROUTE_MISMATCH', 'CANON_RETURN_REQUIRES_INPUT',
                        'CANON_RETURN_DEPTH', 'CANON_EXCHANGE_NOT_FOUND',
                        'CANON_CORRECTION_MISMATCH', 'MEMORY_SOURCE_MISMATCH',
                        'CANON_EDGE_NOT_FOUND', 'CANON_REMOTE_RECEIVER_REQUIRED',
                        'CANON_EDGE_NOT_BOUND', 'CANON_EDGE_SCHEMA_MISMATCH',
                        'CANON_EDGE_CONTRACT_MISMATCH', 'CANON_EDGE_RETURN_MISMATCH', 'CANON_TYPED_BACKFIRE_REQUIRED',
                    }:
                        raise
                    reasons = [error.code]
        return CanonClassification(project_id=self.store.project_id,
            message_digest=content_digest(request.model_dump(exclude={'request_id'})),
            classification='expected' if expected else 'undefined_or_incompatible',
            reasons=reasons, expected_contract=request.expected_contract, observed_at=now(),
            automatic_admission_permitted=expected and automatic,
            receiver_decision_required=not (expected and automatic))

    def send(self,request,lease,*,actor_id,destination_participant=None,return_contract_snapshot=None,_typed_backfire=False):
        request=CanonSend.model_validate(request.model_dump())
        if request.kind == 'backfire' and not _typed_backfire:
            raise LaneError('CANON_TYPED_BACKFIRE_REQUIRED', 'Use task_evidence_input_request for a conditional admitted-input request with an exact recipient and correction trace.')
        self.initialize(lease)
        with lease.transaction('canon') as connection:
            key=content_digest(request.model_dump())
            self.participant(connection,request.sender_id,owner=actor_id)
            prior=self._replay(connection,request.request_id,'send',actor_id,key)
            if prior:return CanonSent(**{**prior,'duplicate':True})
            _, depth = self._validate_message(connection, request, destination_participant=destination_participant,
                check_contract=False,return_contract_snapshot=return_contract_snapshot)
            destination = request.destination_project_id or self.store.project_id
            automatic, reasons = self._compatibility(connection, request) if destination == self.store.project_id else (False, [])
            revision = self.exchange(connection, request.supersedes)[1].get('revision',1)+1 if request.supersedes else 1
            body={'schema':'evidence-lane.canon-envelope.v4', 'project_id':self.store.project_id, 'exchange_id':str(uuid4()),'revision':revision,
                'source_client_id':actor_id, 'message':request.model_dump(exclude={'request_id'}), 'return_depth':depth,'created_at':now(),
                'payload_provenance':'agent_report','native_task_attestation':'not_provided','source_write_granted':False,'plan_mutated':False}
            state = 'sealed' if destination != self.store.project_id else ('admitted' if automatic else 'received')
            result = self._insert_exchange(connection, body, state).model_copy(update={'compatibility_reasons':reasons,
                'receiver_decision_required':state == 'received'})
            if automatic and request.supersedes:
                self._supersede(connection, request.supersedes, result.exchange_id, actor_id=actor_id, basis='receiver_expected_contract')
            self._event(connection,request.request_id,'send',actor_id,key,result.model_dump())
            self.store.append_receipt('canon_outbox_sealed' if state == 'sealed' else 'canon_exchange_received',
                {**result.model_dump(),'source_client_id':actor_id,'compatibility_reasons':reasons,
                    'admission_basis':'not_received' if state == 'sealed' else ('receiver_expected_contract' if automatic else 'pending_receiver')},connection=connection)
            return result

    def _validate_incoming(self, connection, request, snapshot):
        body = snapshot['envelope']
        source, destination = self._route(body)
        if (source != request.source_project_id or source == destination or destination != self.store.project_id
                or content_digest(body) != request.envelope_digest or body['exchange_id'] != request.exchange_id
                or snapshot['source_project_id'] != source or snapshot['envelope_digest'] != request.envelope_digest):
            raise LaneError('CANON_RECEIVE_ROUTE_MISMATCH', 'Receive the exact source-owned envelope in its declared destination project.')
        message = CanonSend.model_validate(body['message'])
        self.participant(connection, message.receiver_id)
        self._expiry(body['message'])
        if snapshot.get('source_reasons'):
            raise LaneError(snapshot['source_reasons'][0], 'The source evidence is not currently eligible for reception.')
        if message.backfire:
            observed = snapshot.get('backfire_observation') or {}
            expected_source = {'project_id':source,'participant_id':message.sender_id}
            if (observed.get('exchange_id') != message.backfire.admitted_exchange_id
                    or observed.get('envelope_digest') != message.backfire.admitted_envelope_digest
                    or observed.get('state') != 'admitted' or observed.get('destination') != expected_source
                    or observed.get('revision',1000000) >= message.backfire.requested_revision):
                raise LaneError('CANON_BACKFIRE_SOURCE_INTEGRITY', 'Verify the exact current source-owned admitted-input observation.')
        self._validate_edge_message(connection, message, source_project_id=source)
        depth = self._validate_return(connection, message, source_project_id=source, destination_project_id=destination,
            source_observation=snapshot)
        if depth != body['return_depth']:
            raise LaneError('CANON_RETURN_DEPTH', 'The immutable return depth differs from the exact local parent.')
        return self._compatibility(connection, message, source_project_id=source)

    def classify_packet(self, request, source_snapshot):
        body = source_snapshot['envelope']
        automatic, reasons = False, []
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            try:
                automatic, reasons = self._validate_incoming(connection, request, source_snapshot)
            except LaneError as error:
                if error.code not in {'CANON_RECEIVE_ROUTE_MISMATCH','CANON_PARTICIPANT_NOT_FOUND',
                        'CANON_EXCHANGE_EXPIRED','MEMORY_SOURCE_MISMATCH','CANON_EXCHANGE_NOT_FOUND',
                        'CANON_EDGE_NOT_FOUND','CANON_EDGE_NOT_BOUND','CANON_EDGE_SCHEMA_MISMATCH',
                        'CANON_EDGE_CONTRACT_MISMATCH','CANON_EDGE_RETURN_MISMATCH','CANON_RETURN_CONTRACT_MISMATCH',
                        'CANON_RETURN_ROUTE_MISMATCH','CANON_RETURN_REQUIRES_INPUT','CANON_RETURN_DEPTH','CANON_CORRECTION_MISMATCH',
                        'CANON_BACKFIRE_REQUIRES_ADMITTED_INPUT','CANON_BACKFIRE_STALE_REVISION_BLOCKED'}:
                    raise
                reasons = [error.code]
        expected = not reasons
        return CanonPacketClassification(project_id=self.store.project_id, source_project_id=request.source_project_id,
            exchange_id=request.exchange_id,envelope_digest=request.envelope_digest,message_digest=content_digest(body['message']),
            classification='expected' if expected else 'undefined_or_incompatible',reasons=reasons,
            expected_contract=body['message']['expected_contract'],observed_at=now(),automatic_admission_permitted=expected and automatic,
            receiver_decision_required=not(expected and automatic))

    def _task_result_message(self, connection, request):
        edge = self._edge(connection, request.edge_id)
        definition, source = edge.edge['definition'], edge.edge['source']
        if definition['destination']['project_id'] != self.store.project_id:
            raise LaneError('CANON_TASK_RESULT_OWNER_MISMATCH', 'Only the edge destination project can seal its typed task result.')
        return CanonSend(request_id=str(uuid5(UUID(request.request_id),'canon:task_result:seal')), sender_id=definition['destination']['participant_id'],
            receiver_id=source['participant_id'], destination_project_id=source['project_id'], kind='result',
            payload=request.payload, expected_contract=definition['expected_return_contract'], reply_to=request.reply_to,
            edge_id=request.edge_id, expires_at=request.expires_at)

    def task_result(self, request, lease, *, actor_id, destination_participant=None):
        self.initialize(lease)
        with lease.transaction('canon') as connection:
            message = self._task_result_message(connection, request)
            return self._seal_operation(connection, request, message, 'task_result', lease,
                actor_id=actor_id,destination_participant=destination_participant)

    def _backfire_message(self, connection, request):
        row, body = self.exchange(connection, request.admitted_exchange_id)
        source = CanonTaskEndpoint(project_id=self.store.project_id, participant_id=row['receiver_id'])
        inherited = (body['message'].get('backfire') or {}).get('trace',[])
        trace = request.trace or [CanonTaskEndpoint.model_validate(item) for item in inherited]
        if (source in trace or request.recipient in trace or source == request.recipient
                or len(trace) > 30 or [item.model_dump() for item in trace[:len(inherited)]] != inherited):
            raise LaneError('CANON_BACKFIRE_ROUTE_CYCLE_BLOCKED', 'Preserve prior correction endpoints and do not revisit a source or recipient.')
        details = CanonBackfireDetails(admitted_exchange_id=request.admitted_exchange_id,
            admitted_envelope_digest=request.admitted_envelope_digest,failure_class=request.failure_class,
            requested_revision=request.requested_revision,dependency_ids=sorted(request.dependency_ids),
            return_route=request.return_route,trace=[*trace,source,request.recipient])
        return CanonSend(request_id=str(uuid5(UUID(request.request_id),'canon:backfire:seal')),
            sender_id=source.participant_id,receiver_id=request.recipient.participant_id,
            destination_project_id=request.recipient.project_id,kind='backfire',payload=request.payload,
            expected_contract=request.requested_contract,return_contract=request.return_contract,
            expires_at=request.expires_at,backfire=details)

    @staticmethod
    def _operation_key(request, kind):
        identity = ({'recipient':request.recipient.model_dump(),'requested_contract':request.requested_contract,
            'requested_revision':request.requested_revision} if kind == 'backfire'
            else {'edge_id':request.edge_id,'result_payload_digest':content_digest(request.payload.model_dump())})
        return content_digest(identity)

    def _operation_record(self, connection, event):
        fields = dict(event)
        digest = fields.pop('digest')
        if content_digest(fields) != digest or event['kind'] not in {'backfire','task_result'}:
            raise LaneError('CANON_OPERATION_INTEGRITY', 'The conditional task exchange authority operation differs from its attributable event.')
        record = json.loads(event['result_json'])
        operation = record['operation']
        kind = event['kind']
        request = (CanonBackfire if kind == 'backfire' else CanonTaskResult).model_validate(operation['request'])
        message = getattr(self, f'_{kind}_message')(connection, request)
        identity = content_digest(message.model_dump(exclude={'request_id','expires_at'}))
        delivery = CanonOperationSent.model_validate(record['delivery'])
        row, body = self.exchange(connection, delivery.exchange_id)
        seal = self._replay(connection, message.request_id, 'send', event['actor_id'],content_digest(message.model_dump()))
        if (event['request_id'] != request.request_id or event['input_digest'] != content_digest(request.model_dump())
                or operation['dedup_key'] != self._operation_key(request,kind) or operation['identity_digest'] != identity
                or delivery.dedup_key != operation['dedup_key'] or record['exchange_id'] != delivery.exchange_id
                or body['message'] != message.model_dump(exclude={'request_id'}) or body['source_client_id'] != event['actor_id']
                or row['envelope_digest'] != delivery.envelope_digest or not seal
                or seal['exchange_id'] != delivery.exchange_id or seal['envelope_digest'] != delivery.envelope_digest
                or seal['state'] != delivery.state or seal['local_role'] != delivery.local_role):
            raise LaneError('CANON_OPERATION_INTEGRITY', 'The deduplication key, request and immutable source seal must name the same operation.')
        return operation, row

    def _seal_operation(self, connection, request, message, kind, lease, *, actor_id,
            destination_participant=None,return_contract_snapshot=None):
        self.participant(connection, message.sender_id, owner=actor_id)
        key = self._operation_key(request,kind)
        identity = content_digest(message.model_dump(exclude={'request_id','expires_at'}))
        request_digest = content_digest(request.model_dump())
        event = connection.execute('SELECT * FROM canon_events WHERE request_id=?',(request.request_id,)).fetchone()
        canonical = None
        if event:
            if event['kind'] not in {kind,kind+'_replay'}:
                raise LaneError('CANON_REQUEST_CONFLICT', 'This request ID names another operation.')
            recorded = self._replay(connection, request.request_id, event['kind'], actor_id, request_digest)
            canonical = event if event['kind'] == kind else connection.execute(
                'SELECT * FROM canon_events WHERE request_id=?',(recorded['canonical_request_id'],)).fetchone()
            if canonical is None or canonical['kind'] != kind:
                raise LaneError('CANON_OPERATION_INTEGRITY', 'A replay must point to its existing canonical operation.')
        if canonical is None:
            canonical = connection.execute("SELECT * FROM canon_events WHERE kind=? "
                "AND json_extract(result_json,'$.operation.dedup_key')=?",(kind,key)).fetchone()
        if canonical:
            operation, row = self._operation_record(connection, canonical)
            if operation['dedup_key'] != key or operation['identity_digest'] != identity:
                code = 'CANON_BACKFIRE_DEDUP_CONFLICT' if kind == 'backfire' else 'CANON_TASK_RESULT_DEDUP_CONFLICT'
                raise LaneError(code, 'The same semantic key already names different exact task exchange authority request content.')
            if event is None:
                self._event(connection,request.request_id,kind+'_replay',actor_id,request_digest,
                    {'exchange_id':row['exchange_id'],'canonical_request_id':canonical['request_id'],'dedup_key':key})
            return CanonOperationSent(**self._delivery_result(connection,row,duplicate=True).model_dump(),dedup_key=key)
        result = self.send(message,lease,actor_id=actor_id,destination_participant=destination_participant,
            return_contract_snapshot=return_contract_snapshot,_typed_backfire=kind == 'backfire')
        delivery = CanonOperationSent(**result.model_dump(),dedup_key=key)
        self._event(connection,request.request_id,kind,actor_id,request_digest,
            {'exchange_id':delivery.exchange_id,'delivery':delivery.model_dump(),
                'operation':{'dedup_key':key,'identity_digest':identity,'request':request.model_dump()}})
        self.store.append_receipt('canon_backfire_proposed' if kind == 'backfire' else 'canon_task_result_sealed',
            delivery.model_dump(),connection=connection)
        return delivery

    def backfire(self, request, lease, *, actor_id,destination_participant=None,return_contract_snapshot=None):
        self.initialize(lease)
        with lease.transaction('canon') as connection:
            message = self._backfire_message(connection,request)
            self.participant(connection,message.sender_id,owner=actor_id)
            self._validate_backfire_source(connection,message)
            return self._seal_operation(connection,request,message,'backfire',lease,actor_id=actor_id,
                destination_participant=destination_participant,return_contract_snapshot=return_contract_snapshot)

    def receive(self, request, lease, *, actor_id, source_snapshot):
        self.initialize(lease)
        body = source_snapshot['envelope']
        source, destination = self._route(body)
        if (source != request.source_project_id or source == destination or destination != self.store.project_id
                or content_digest(body) != request.envelope_digest or body['exchange_id'] != request.exchange_id
                or source_snapshot['source_project_id'] != source or source_snapshot['envelope_digest'] != request.envelope_digest):
            raise LaneError('CANON_RECEIVE_ROUTE_MISMATCH', 'Receive the exact source-owned envelope in its declared destination project.')
        message = CanonSend.model_validate(body['message'])
        with lease.transaction('canon') as connection:
            self.participant(connection, message.receiver_id, owner=actor_id)
            key=content_digest(request.model_dump())
            prior=self._replay(connection,request.request_id,'receive',actor_id,key)
            if prior:
                row, _ = self.exchange(connection, request.exchange_id)
                return self._delivery_result(connection, row, duplicate=True)
            existing = connection.execute('SELECT exchange_id FROM canon_exchanges WHERE exchange_id=?',(request.exchange_id,)).fetchone()
            if existing:
                row, original = self.exchange(connection, request.exchange_id)
                if row['envelope_digest'] != request.envelope_digest or original != body:
                    raise LaneError('CANON_ENVELOPE_IMMUTABILITY_CONFLICT', 'The exchange ID already names other immutable bytes.')
                return self._delivery_result(connection, row, duplicate=True)
            automatic, reasons = self._validate_incoming(connection, request, source_snapshot)
            result = self._insert_exchange(connection, body, 'admitted' if automatic else 'received').model_copy(
                update={'compatibility_reasons':reasons,'receiver_decision_required':not automatic})
            if automatic and message.supersedes:
                self._supersede(connection, message.supersedes, result.exchange_id, actor_id=actor_id, basis='receiver_expected_contract')
            observation={key:source_snapshot[key] for key in ('source_project_id','envelope_digest','parent_observation',
                'backfire_observation','source_history','source_seal')}
            self._event(connection,request.request_id,'receive',actor_id,key,result.model_dump())
            self.store.append_receipt('canon_exchange_received',{**result.model_dump(),'source_observation':observation,
                'compatibility_reasons':reasons,'admission_basis':'receiver_expected_contract' if automatic else 'pending_receiver'},connection=connection)
            return result

    def decide(self,request,lease,*,actor_id,source_snapshot=None):
        self.initialize(lease)
        with lease.transaction('canon') as connection:
            key=content_digest(request.model_dump())
            row,body=self.exchange(connection,request.exchange_id)
            if row['local_role'] == 'outbox':
                raise LaneError('CANON_RECEIVER_PROJECT_REQUIRED', 'Only the receiving project can decide an input.')
            self.participant(connection,row['receiver_id'],owner=actor_id)
            prior=self._replay(connection,request.request_id,'decide',actor_id,key)
            if prior:return CanonDecisionResult(**prior)
            if row['envelope_digest']!=request.envelope_digest or row['version']!=request.expected_version:
                raise LaneError('CANON_DECISION_VERSION_CONFLICT','Select the exact exchange digest and current state version.')
            if row['state'] not in {'received','needs_clarification'}:
                raise LaneError('CANON_DECISION_STATE','Only pending or clarification-requested input can be decided.')
            message=CanonSend.model_validate(body['message'])
            source, destination = self._route(body)
            _, reasons = self._compatibility(connection, message, source_project_id=source)
            if request.decision=='admit':
                self._expiry(body['message'])
                self._validate_edge_message(connection, message, source_project_id=source)
                if reasons and request.incompatible_input_decision != 'ACCEPT':
                    raise LaneError('CANON_INPUT_ACCEPT_REQUIRED', 'This undefined or incompatible input requires the receiver-owned explicit ACCEPT decision.')
                if source == self.store.project_id:
                    for ref in message.payload.references:ProjectMemory(self.store)._reference(connection,ref)
                elif (not source_snapshot or source_snapshot['envelope'] != body
                        or source_snapshot['envelope_digest'] != row['envelope_digest'] or source_snapshot['source_project_id'] != source):
                    raise LaneError('CANON_SOURCE_ENVELOPE_MISMATCH', 'Reverify the exact source packet before admitting its foreign evidence.')
                self._validate_return(connection, message, source_project_id=source, destination_project_id=destination,
                    source_observation=source_snapshot)
            elif request.incompatible_input_decision is not None:
                raise LaneError('CANON_INPUT_DECISION_MISMATCH', 'ACCEPT only accompanies the admit decision.')
            state={'admit':'admitted','reject':'rejected','clarify':'needs_clarification'}[request.decision]
            connection.execute('UPDATE canon_exchanges SET state=?,version=version+1,state_digest=? WHERE exchange_id=?',
                (state,self._state_digest(request.exchange_id,row['envelope_digest'],state,row['version']+1),request.exchange_id))
            if request.decision == 'admit' and message.supersedes:
                self._supersede(connection, message.supersedes, request.exchange_id,
                    actor_id=actor_id, basis='receiver_correction_admission')
            result=CanonDecisionResult(exchange_id=request.exchange_id,state=state,version=row['version']+1,
                receiver_id=row['receiver_id'],reason=request.reason,incompatible_input_decision=request.incompatible_input_decision,
                compatibility_reasons=reasons)
            self._event(connection,request.request_id,'decide',actor_id,key,result.model_dump())
            self.store.append_receipt('canon_receiver_decision',{**result.model_dump(),'source_client_id':actor_id},connection=connection)
            return result

    def read(self,request=None):
        request=request or CanonRead()
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            if not self._table(connection,'canon_exchanges'):
                return CanonPage(project_id=self.store.project_id,participants=[],contracts=[],exchanges=[],last_sequence=request.after_sequence,truncated=False,participants_truncated=False,contracts_truncated=False)
            participants=connection.execute('SELECT participant_id FROM canon_participants ORDER BY created_at,participant_id LIMIT 51').fetchall()
            ids=[]
            used=0
            for row in participants[:50]:
                item=self.participant(connection,row[0])
                size=len(json_text(item).encode())
                if used+size>request.max_bytes//8:break
                ids.append(item)
                used+=size
            contracts=connection.execute('SELECT contract_digest FROM canon_contract_current ORDER BY receiver_id,contract_key LIMIT 21').fetchall()
            values=[]
            for row in contracts[:20]:
                item={'contract_digest':row[0],**self._contract(connection,row[0])}
                size=len(json_text(item).encode())
                if used+size>request.max_bytes//3:break
                values.append(item)
                used+=size
            where,parameters='sequence>?',[request.after_sequence]
            if request.participant_id:
                where+=' AND ((source_project_id=? AND sender_id=?) OR (destination_project_id=? AND receiver_id=?))'
                parameters.extend([self.store.project_id,request.participant_id,self.store.project_id,request.participant_id])
            rows=connection.execute('SELECT exchange_id FROM canon_exchanges WHERE '+where+' ORDER BY sequence LIMIT ?',[*parameters,request.limit+1]).fetchall()
            items=[]
            used+=1024  # response envelope and collection framing
            for entry in rows[:request.limit]:
                row,body=self.exchange(connection,entry[0])
                item={key:row[key] for key in ['sequence','exchange_id','sender_id','receiver_id','kind','envelope_digest','state','version','parent_id','created_at','source_project_id','destination_project_id','local_role','artifact_path']}
                item.update({'source_client_id':body['source_client_id'],'summary':body['message']['payload']['summary'],
                             'expires_at':body['message']['expires_at'],'native_task_attestation':'not_provided'})
                if row['state'] == 'superseded':
                    supersession = self._supersession(connection, row)
                    item['supersession'] = supersession.model_dump() if supersession else None
                    item['supersession_evidence'] = 'recorded_event' if supersession else 'historical_link_unavailable'
                if request.include_payload:item['envelope']=body
                size=len(json_text(item).encode())
                if used+size>request.max_bytes:break
                used+=size
                items.append(item)
            return CanonPage(project_id=self.store.project_id,participants=ids,contracts=values,exchanges=items,
                last_sequence=items[-1]['sequence'] if items else request.after_sequence,truncated=len(items)<len(rows),
                participants_truncated=len(ids)<len(participants),contracts_truncated=len(values)<len(contracts))

    def _verify_history(self,connection,*,limit=50000):
        if not 1<=limit<=50000:raise LaneError('CANON_HISTORY_BUDGET','Use a bounded history verification limit.')
        rows=connection.execute('SELECT * FROM canon_events ORDER BY sequence LIMIT ?',(limit+1,)).fetchall() if self._table(connection,'canon_events') else []
        if len(rows)>limit:raise LaneError('CANON_HISTORY_BUDGET','History exceeds the selected budget.')
        previous=None
        for sequence,row in enumerate(rows,1):
            body=dict(row)
            digest=body.pop('digest')
            if row['sequence']!=sequence or row['previous_digest']!=previous or content_digest(body)!=digest:
                raise LaneError('CANON_HISTORY_INTEGRITY','The task exchange authority event chain is inconsistent.')
            previous=digest
        return {'events_verified':len(rows),'head':previous}

    def verify_history(self,*,limit=50000):
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            return self._verify_history(connection,limit=limit)

    def continuation_checkpoint(self, participant_ids, *, limit=256):
        """Read a complete bounded pending set; the continuation owner seals it.

        The caller holds the project mutation lock or a coherent snapshot.
        Packet content and receiver decisions stay with the task exchange authority owner.
        """
        ids = sorted(set(participant_ids))
        if (not 1 <= len(ids) <= 32 or len(ids) != len(participant_ids)
                or any(not re.fullmatch(UUID_PATTERN, item) for item in ids)
                or not 1 <= limit <= 256):
            raise LaneError('CANON_CONTINUITY_BUDGET', 'Select one to 32 exact participants and at most 256 pending exchanges.')
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            for identity in ids:
                self.participant(connection, identity)
            history = self._verify_history(connection)
            placeholders = ','.join('?' for _ in ids)
            rows = connection.execute("SELECT exchange_id FROM canon_exchanges WHERE "
                "state IN ('sealed','received','needs_clarification') AND "
                f"((source_project_id=? AND sender_id IN ({placeholders})) OR "
                f"(destination_project_id=? AND receiver_id IN ({placeholders}))) ORDER BY sequence LIMIT ?",
                [self.store.project_id, *ids, self.store.project_id, *ids, limit + 1]).fetchall()
            if len(rows) > limit:
                raise LaneError('CANON_CONTINUITY_BUDGET', 'Resolve pending exchanges before offering a complete bounded continuation.')
            pending = []
            for entry in rows:
                row, body = self.exchange(connection, entry['exchange_id'])
                pending.append(CanonContinuationLocator(**{key: row[key] for key in [
                    'sequence', 'exchange_id', 'envelope_digest', 'source_project_id', 'destination_project_id',
                    'sender_id', 'receiver_id', 'state', 'version']}, expected_contract=body['message']['expected_contract']))
            return CanonContinuationCheckpoint(project_id=self.store.project_id, participant_ids=ids,
                event_sequence=history['events_verified'], event_head=history['head'], pending=pending)

    def inbox(self,request):
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            initialized=self._table(connection,'canon_participants')
            if request.receiver_id and not initialized:
                raise LaneError('CANON_PARTICIPANT_NOT_FOUND','Select an existing receiver participant in this project.')
            receiver=self.participant(connection,request.receiver_id) if request.receiver_id else None
            # Verify the chain before using result JSON to select packet events.
            # A damaged exchange_id key must not silently hide decision history.
            history=self._verify_history(connection,limit=request.history_limit)
            clauses=['sequence>?', 'destination_project_id=?']
            parameters=[request.after_sequence, self.store.project_id]
            if request.receiver_id:
                clauses.append('receiver_id=?')
                parameters.append(request.receiver_id)
            if request.exchange_id:
                if not initialized:
                    raise LaneError('CANON_EXCHANGE_NOT_FOUND','The exchange is not recorded in this project.')
                row,_=self.exchange(connection,request.exchange_id)
                if row['local_role']=='outbox' or request.receiver_id and row['receiver_id']!=request.receiver_id:
                    raise LaneError('CANON_INBOX_ROUTE_MISMATCH','The selected exchange belongs to another receiver inbox.')
                clauses.append('exchange_id=?')
                parameters.append(request.exchange_id)
            if request.states:
                clauses.append('state IN ('+','.join('?' for _ in request.states)+')')
                parameters.extend(request.states)
            rows=connection.execute('SELECT exchange_id FROM canon_exchanges WHERE '+' AND '.join(clauses)
                +' ORDER BY sequence LIMIT ?',[*parameters,request.limit+1]).fetchall() if initialized else []
            packets=[]
            used=4096+len(json_text(receiver).encode())
            for entry in rows[:request.limit]:
                row,body=self.exchange(connection,entry[0])
                message=body['message']
                events=connection.execute("SELECT * FROM canon_events WHERE json_extract(result_json,'$.exchange_id')=? "
                    'AND sequence>? ORDER BY sequence LIMIT ?',
                    (row['exchange_id'],request.after_event_sequence,request.events_per_packet+1)).fetchall()
                timeline=[]
                for event in events[:request.events_per_packet]:
                    result=json.loads(event['result_json'])
                    item={key:event[key] for key in ('sequence','request_id','kind','actor_id','input_digest','previous_digest','digest','created_at')}
                    if event['kind'] in {'backfire','task_result'}:
                        item['stored_result_digest'] = content_digest(result)
                        operation_request = result['operation'].pop('request')
                        result['operation']['request_digest'] = content_digest(operation_request)
                        item['result_projection'] = 'operation_metadata_without_request_payload'
                    item['result']=result
                    if event['kind']=='decide':
                        item['reason_evidence']='recorded_event' if result.get('reason') else 'reason_not_in_event'
                    timeline.append(item)
                packet={key:row[key] for key in ('sequence','exchange_id','sender_id','receiver_id','kind','envelope_digest','state','version','created_at','source_project_id','destination_project_id','local_role','artifact_path')}
                packet.update({'source_client_id':body['source_client_id'],'expected_contract':message['expected_contract'],
                    'return_contract':message['return_contract'],'reply_to':message['reply_to'],'supersedes':message['supersedes'],
                    'edge_id':message.get('edge_id'),'revision':body.get('revision',1),'backfire':message.get('backfire'),
                    'payload_digest':content_digest(message['payload']),'evidence_refs':message['payload']['references'],
                    'expires_at':message['expires_at'],'events':timeline,'events_truncated':len(timeline)<len(events),
                    'last_event_sequence':timeline[-1]['sequence'] if timeline else request.after_event_sequence,
                    'raw_payload_returned':False,'admission_at_receipt':self._admission_evidence(connection,row['exchange_id'])})
                if row['state']=='superseded':
                    link=self._supersession(connection,row)
                    packet['supersession']=link.model_dump() if link else None
                    packet['supersession_evidence']='recorded_event' if link else 'historical_link_unavailable'
                size=len(json_text(packet).encode())
                if used+size>request.max_bytes:
                    if not packets:
                        raise LaneError('CANON_INBOX_ITEM_BUDGET','Select fewer events per packet or a larger response byte budget.')
                    break
                packets.append(packet)
                used+=size
            result={'project_id':self.store.project_id,'receiver':receiver,'packets':packets,'packet_count':len(packets),
                'last_sequence':packets[-1]['sequence'] if packets else request.after_sequence,
                'truncated':len(packets)<len(rows),'history':history,'raw_payload_returned':False,'project_mutated':False,
                'native_task_attestation':'not_provided'}
            return CanonInboxPage(**result,projection_digest=content_digest(result))

    def inspect(self,request):
        tables=('canon_participants','canon_contracts','canon_contract_current','canon_exchanges','canon_events','canon_supersessions',
            'canon_task_edges','canon_task_edge_bindings')
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            counts={table:connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                if self._table(connection,table) else 0 for table in tables}
            if sum(counts.values())>request.record_limit:
                raise LaneError('CANON_INSPECTION_BUDGET','task exchange authority inspection exceeds the selected total record budget.')
            integrity=[row[0] for row in connection.execute('PRAGMA quick_check')]
            foreign_keys=[list(row) for row in connection.execute('PRAGMA foreign_key_check')]
            if integrity!=['ok'] or foreign_keys:
                raise LaneError('CANON_DATABASE_INTEGRITY','task exchange authority database or foreign-key checks failed.')
            history=self._verify_history(connection,limit=request.record_limit)
            initialized=self._table(connection,'canon_participants')
            migrations=[]
            states={}
            verified={'participants':0,'contracts':0,'current_contracts':0,'exchanges':0,'supersession_links':0,'conditional_operations':0,
                'task_edges':0,'task_edge_bindings':0}
            missing=0
            if initialized:
                migrations=[dict(row) for row in connection.execute('SELECT owner,version,digest FROM schema_migrations WHERE owner=? ORDER BY version',('canon',))]
                expected=[{'owner':m.owner,'version':m.version,'digest':m.digest} for m in CANON_MIGRATIONS]
                if migrations!=expected:
                    raise LaneError('CANON_SCHEMA_INTEGRITY','task exchange authority migration identities differ from the executable owner.')
                verify_schema_history_files(self.store,connection)
                for row in connection.execute('SELECT participant_id FROM canon_participants'):
                    self.participant(connection,row[0])
                    verified['participants']+=1
                for row in connection.execute('SELECT contract_digest FROM canon_contracts'):
                    self._contract(connection,row[0])
                    verified['contracts']+=1
                for row in connection.execute('SELECT * FROM canon_contract_current'):
                    contract=self._contract(connection,row['contract_digest'])['contract']
                    if (row['receiver_id'],row['contract_key'])!=(contract['receiver_id'],contract['contract_key']):
                        raise LaneError('CANON_CONTRACT_INTEGRITY','A current contract pointer differs from its exact receiver and key.')
                    verified['current_contracts']+=1
                for entry in connection.execute('SELECT exchange_id FROM canon_exchanges ORDER BY sequence'):
                    row,_=self.exchange(connection,entry[0])
                    verified['exchanges']+=1
                    states[row['state']]=states.get(row['state'],0)+1
                    if row['state']=='superseded':
                        if self._supersession(connection,row):
                            verified['supersession_links']+=1
                        else:
                            missing+=1
                # Include links whose target state was damaged, not only the
                # links reached by selecting currently superseded exchanges.
                for entry in connection.execute('SELECT exchange_id FROM canon_supersessions'):
                    row,_=self.exchange(connection,entry[0])
                    self._supersession(connection,row)
                edges=self._graph_edges(connection,limit=request.record_limit)
                verified['task_edges']=len(edges)
                verified['task_edge_bindings']=sum(edge.destination_bound for edge in edges)
                for event in connection.execute("SELECT * FROM canon_events WHERE kind IN ('task_result','backfire') ORDER BY sequence"):
                    self._operation_record(connection,event)
                    verified['conditional_operations'] += 1
            scope=['sqlite_quick_check','foreign_keys','event_chain']
            if initialized:
                scope+=['owner_migrations_and_history_files','participant_and_contract_digests',
                    'current_contract_pointers','envelope_and_state_digests','recorded_supersession_links',
                    'recorded_task_edge_dag','task_edge_files_and_destination_bindings','packet_project_roles_and_registered_files',
                    'conditional_operation_keys_requests_and_source_seals']
            result={'project_id':self.store.project_id,'initialized':initialized,'counts':counts,'packet_states':states,
                'integrity':integrity,'foreign_key_errors':foreign_keys,'history':history,'applied_migrations':migrations,
                'schema_contract':lane_schema_asset('canon'),'objects_verified':verified,
                'historical_supersession_links_unavailable':missing,'verified_scope':scope,
                'unverified_scope':['native_task_attestation','cross_project_graph_completeness','cross_project_dispatch','receipt_lane_integrity'],
                'raw_payload_returned':False,'project_mutated':False,'native_task_attestation':'not_provided'}
            return CanonInspection(**result,projection_digest=content_digest(result))


def register_canon_actions(engine):
    def remote_context(context, project_id, action='task_evidence_graph'):
        from .lane_reader import LaneReader
        target = LaneReader(engine)._context(context, project_id)
        engine.registry.validate(action, {}, target)
        return target

    def remote_read(context, project_id, read, action='task_evidence_graph'):
        import time

        from .storage import bounded_project_read
        remote_context(context, project_id, action)
        project = engine.directory.open(project_id)
        with bounded_project_read(project.root, time.monotonic() + 10), project.lane('canon').connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            canon = CanonStore(project)
            canon._verify_history(connection, limit=50000)
            value = read(canon, connection)
        remote_context(context, project_id, action)
        project.assert_current_binding()
        return value

    def task_edge_register(context, request):
        destination = None
        remote_id = request.destination.project_id
        if remote_id != context.project_id:
            destination = remote_read(context, remote_id,
                lambda canon, connection:canon.participant(connection, request.destination.participant_id))
        store = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(store, kind='capture') as lease:
            if remote_id != context.project_id:
                remote_context(context, remote_id)
            with lease.coordinated_transaction(['canon', 'receipts']):
                return CanonStore(store).register_task_edge(request, lease, actor_id=context.client_id,
                    destination_participant=destination)

    def task_edge_bind(context, request):
        source = None
        if request.source_project_id != context.project_id:
            source = remote_read(context, request.source_project_id,
                lambda canon, connection:canon._edge(connection, request.edge_id))
        store = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(store, kind='capture') as lease:
            if request.source_project_id != context.project_id:
                remote_context(context, request.source_project_id)
            with lease.coordinated_transaction(['canon', 'receipts']):
                return CanonStore(store).bind_task_edge(request, lease, actor_id=context.client_id, source_edge=source)

    for name, description, input_model, handler in [
        ('task_evidence_edge_register', 'Seal an owned acyclic task edge and declared contract scope; never create a host task or grant execution.',
            CanonTaskEdgeRegister, task_edge_register),
        ('task_evidence_edge_bind', 'Bind the exact source-recorded task edge as its destination participant, using authorized source-project reads.',
            CanonTaskEdgeBind, task_edge_bind)]:
        engine.registry.register(ActionSpec(name, description, input_model, CanonTaskEdgeResult, handler,
            permission='write', profile='canon', mutates=True, workflow='exchange-task-evidence'))
    def packet_mutation(method):
        def run(context, request):
            foreign, destination, snapshot, remote_ids = [], None, None, set()
            return_snapshot = None
            message = request
            if method in {'task_result','backfire'}:
                project = engine.directory.open(context.project_id)
                with project.lane('canon').connection(read_only=True) as connection:
                    message = getattr(CanonStore(project),f'_{method}_message')(connection, request)
            if method == 'backfire' and request.return_route.project_id != context.project_id:
                return_snapshot = remote_read(context, request.return_route.project_id,
                    lambda canon, connection:canon._return_contract_snapshot(connection,request.return_contract),'task_evidence_read')
                remote_ids.add(request.return_route.project_id)
            if method == 'expect':
                for endpoint in request.sender_endpoints:
                    if endpoint.project_id != context.project_id:
                        foreign.append(remote_read(context, endpoint.project_id,
                            lambda canon, connection, endpoint=endpoint:canon.participant(connection, endpoint.participant_id), 'task_evidence_read'))
                        remote_ids.add(endpoint.project_id)
            elif method in {'send','task_result','backfire'} and message.destination_project_id and message.destination_project_id != context.project_id:
                destination = remote_read(context, message.destination_project_id,
                    lambda canon, connection:canon.participant(connection, message.receiver_id), 'task_evidence_read')
                remote_ids.add(message.destination_project_id)
            elif method == 'receive':
                if request.source_project_id == context.project_id:
                    raise LaneError('CANON_RECEIVE_ROUTE_MISMATCH', 'Local sends already record their receiver input in this project.')
                snapshot = remote_read(context, request.source_project_id,
                    lambda canon, connection:canon.source_snapshot(connection, request), 'task_evidence_read')
                remote_ids.add(request.source_project_id)
            elif method == 'decide' and request.decision == 'admit':
                project = engine.directory.open(context.project_id)
                with project.lane('canon').connection(read_only=True) as connection:
                    row, _ = CanonStore(project).exchange(connection, request.exchange_id)
                    source_id, digest = row['source_project_id'], row['envelope_digest']
                if source_id != context.project_id:
                    locator = CanonReceive(source_project_id=source_id, exchange_id=request.exchange_id, envelope_digest=digest)
                    snapshot = remote_read(context, source_id, lambda canon, connection:canon.source_snapshot(connection, locator), 'task_evidence_read')
                    remote_ids.add(source_id)
            store = engine.directory.open(context.project_id, write=True)
            with engine.project_work.mutation(store, kind='capture') as lease:
                for project_id in remote_ids:
                    remote_context(context, project_id, 'task_evidence_read')
                with lease.coordinated_transaction(['canon','receipts']):
                    options = {'expect':{'foreign_participants':foreign}, 'send':{'destination_participant':destination},
                        'receive':{'source_snapshot':snapshot}, 'decide':{'source_snapshot':snapshot}, 'task_result':{'destination_participant':destination},
                        'backfire':{'destination_participant':destination,'return_contract_snapshot':return_snapshot}}[method]
                    return getattr(CanonStore(store), method)(request, lease, actor_id=context.client_id, **options)
        return run

    def mutation(method):
        def run(context,request):
            store=engine.directory.open(context.project_id,write=True)
            with engine.project_work.mutation(store,kind='capture') as lease:  # noqa: SIM117 - initialize and writes share this dependent transaction
                with lease.coordinated_transaction(['canon', 'receipts']):
                    return getattr(CanonStore(store),method)(request,lease,actor_id=context.client_id)
        return run
    for name,description,input_model,output_model,method in [
        ('task_evidence_participant_register','Register a project participant owned by this engine client; does not create a host task.',CanonJoin,CanonParticipant,'join'),
        ('task_evidence_expect','Version the receiver-owned typed expected-input contract.',CanonExpected,CanonContractResult,'expect'),
        ('task_evidence_send','Seal a foreign-project outbox packet, or receive a local participant exchange against its exact contract.',CanonSend,CanonSent,'send'),
        ('task_evidence_receive','Receive an exact source-sealed packet as its destination participant; never mutate the source project.',CanonReceive,CanonSent,'receive'),
        ('task_evidence_result','Seal or reuse the content-derived typed result for the exact bound edge and pinned return contract.',CanonTaskResult,CanonOperationSent,'task_result'),
        ('task_evidence_input_request','Propose a deduplicated conditional input request from an admitted packet to an exact recipient; never retry automatically.',CanonBackfire,CanonOperationSent,'backfire'),
        ('task_evidence_decide','Admit, reject or request clarification on the exact receiver-owned input.',CanonDecide,CanonDecisionResult,'decide'),
        ('task_evidence_supersede','Supersede a decided input with an exact newer admitted exchange on the same receiver-owned route.',CanonSupersede,CanonSuperseded,'supersede')]:
        engine.registry.register(ActionSpec(name,description,input_model,output_model,packet_mutation(method) if method in {'expect','send','receive','decide','task_result','backfire'} else mutation(method),permission='write',profile='canon',mutates=True, workflow='exchange-task-evidence'))
    engine.registry.register(ActionSpec('task_evidence_read','Inspect bounded project participants, expected inputs and exchange history.',CanonRead,CanonPage,
        lambda context,request:CanonStore(engine.directory.open(context.project_id)).read(request),profile='canon',queryable_in_delta=True,
        cross_project_read=True,read_migrations=CANON_MIGRATIONS, workflow='exchange-task-evidence'))
    def classify_packet(context, request):
        if request.source_project_id == context.project_id:
            raise LaneError('CANON_RECEIVE_ROUTE_MISMATCH', 'Use task_evidence_classify for an unsealed local message.')
        snapshot = remote_read(context, request.source_project_id,
            lambda canon, connection:canon.source_snapshot(connection, request, preview=True), 'task_evidence_read')
        return CanonStore(engine.directory.open(context.project_id)).classify_packet(request, snapshot)

    engine.registry.register(ActionSpec('task_evidence_packet_classify',
        'Preview an exact foreign source packet against the current receiving project without storing or admitting it.',
        CanonReceive,CanonPacketClassification,classify_packet,profile='canon',queryable_in_delta=True,
        read_migrations=CANON_MIGRATIONS,workflow='exchange-task-evidence'))
    engine.registry.register(ActionSpec('task_evidence_classify',
        'Classify a proposed task exchange authority message against the current receiver contract without storing or admitting it.',
        CanonSend, CanonClassification,
        lambda context, request: CanonStore(engine.directory.open(context.project_id)).classify(request),
        profile='canon', queryable_in_delta=True, read_migrations=CANON_MIGRATIONS, workflow='exchange-task-evidence'))
    for name,description,input_model,output_model,method in [
        ('task_evidence_inbox','Read project inboxes or one exact receiver with state filters, metadata and bounded attributed events.',
            CanonInbox,CanonInboxPage,'inbox'),
        ('task_evidence_inspect','Verify bounded task exchange authority database, schema, object digests and event-chain state without modifying it.',
            CanonInspect,CanonInspection,'inspect'),
        ('task_evidence_graph','Inspect the full bounded recorded task DAG, destination bindings and locally admitted edge returns.',
            CanonTaskGraphRead,CanonTaskGraph,'task_graph')]:
        engine.registry.register(ActionSpec(name,description,input_model,output_model,
            lambda context,request,method=method:getattr(CanonStore(engine.directory.open(context.project_id)),method)(request),
            profile='canon',queryable_in_delta=True,cross_project_read=True,read_migrations=CANON_MIGRATIONS,workflow='exchange-task-evidence'))
