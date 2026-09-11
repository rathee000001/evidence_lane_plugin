"""Bounded Project Memory locators, typed links and attributed continuity.

Adapts the base owner's content identities, FTS5/BM25 retrieval, suppression and
checkpoint slices. Memory indexes references, never imports other authorities
or reads host memory. Queries and rehydration do not write or attach a task.
"""
from __future__ import annotations

import json
import re
import time
from contextlib import nullcontext
from typing import Literal
from uuid import UUID, uuid4, uuid5

from pydantic import Field, model_validator

from .errors import LaneError
from .lanes import LANE_REGISTRY, is_named_custom_lane
from .migrations import Migration, apply_migrations
from .plan_runtime import TASK_ID_PATTERN, PlanStore, content_digest
from .redaction import contains_secret
from .registry import ActionSpec, Contract, SearchRoute
from .sdk import UUID_PATTERN
from .storage import LaneStore, json_text, now

DIGEST = r'^[0-9a-f]{64}$'
ReferenceKind = Literal['plan_task', 'lineage_event', 'learning_version', 'receipt', 'source_object', 'canon_exchange']
EdgeKind = Literal['DERIVED_FROM', 'EVIDENCES', 'LEARNED_FROM', 'MAPS_TO', 'RELATED_TO', 'REVOKES', 'SUPERSEDES', 'SUPPRESSES']
SUPPRESSING = ('REVOKES', 'SUPERSEDES', 'SUPPRESSES')


class MemoryReference(Contract):
    kind: ReferenceKind
    key: str = Field(pattern=TASK_ID_PATTERN)
    digest: str = Field(pattern=DIGEST)
    revision: int | None = Field(default=None, ge=1)
    profile: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_]{0,63}$')
    lane_id: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_]{0,63}$')

    @model_validator(mode='after')
    def relevant_fields(self):
        if (self.kind == 'plan_task') != (self.revision is not None):
            raise ValueError('Only a Plan task reference requires a revision')
        if (self.kind == 'source_object') != (self.profile is not None):
            raise ValueError('Only a source object requires an attributed profile')
        if self.kind == 'source_object' and self.key != self.digest:
            raise ValueError('A source object key must be its digest')
        if (self.kind == 'source_object') != (self.lane_id is not None):
            raise ValueError('Only a source object requires its explicit owning lane')
        if self.lane_id is not None and self.lane_id not in LANE_REGISTRY and not is_named_custom_lane(self.lane_id):
            raise ValueError('Use the canonical retained lane that owns this object')
        return self


class MemoryLocator(Contract):
    reference: MemoryReference
    label: str = Field(min_length=1, max_length=200)
    search_terms: list[str] = Field(default_factory=list, max_length=24)

    @model_validator(mode='after')
    def bounded_metadata(self):
        if (not self.label.strip() or len(set(self.search_terms)) != len(self.search_terms)
                or any(not term.strip() or len(term) > 192 for term in self.search_terms)
                or len(self.model_dump_json().encode()) > 8192):
            raise ValueError('Use distinct, bounded locator labels and terms')
        if contains_secret(self.model_dump()):
            raise ValueError('Secrets cannot enter Memory locator metadata')
        return self


class MemoryEdge(Contract):
    source_id: str = Field(pattern=DIGEST)
    target_id: str = Field(pattern=DIGEST)
    kind: EdgeKind
    evidence: MemoryReference

    @model_validator(mode='after')
    def distinct_endpoints(self):
        if self.source_id == self.target_id:
            raise ValueError('An edge must connect distinct locators')
        return self


class MemoryIngest(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    locators: list[MemoryLocator] = Field(default_factory=list, max_length=100)
    edges: list[MemoryEdge] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def bounded_batch(self):
        if not (self.locators or self.edges) or len(self.model_dump_json().encode()) > 262_144:
            raise ValueError('Provide a nonempty batch within 256 KiB')
        return self


class MemoryIngested(Contract):
    project_id: str
    locator_ids: list[str]
    edge_ids: list[str]
    head: str
    duplicate: bool = False
    authority: Literal['project_memory'] = 'project_memory'
    link_semantics: Literal['agent_report', 'engine_verified_exit'] = 'agent_report'
    source_authorities_mutated: bool = False


class MemoryRecordLink(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    source: MemoryLocator
    target: MemoryLocator
    edge_type: EdgeKind
    evidence: MemoryReference

    @model_validator(mode='after')
    def distinct_locators(self):
        if self.source == self.target:
            raise ValueError('A link must connect distinct locators')
        return self


class MemoryRead(Contract):
    query: str | None = Field(default=None, min_length=1, max_length=500)
    kinds: list[ReferenceKind] = Field(default_factory=list, max_length=6)
    profile: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_]{0,63}$')
    limit: int = Field(default=8, ge=1, le=20)
    include_history: bool = False
    max_bytes: int = Field(default=65_536, ge=32_768, le=262_144)


class MemoryPage(Contract):
    project_id: str
    locators: list[dict]
    head: str | None
    truncated: bool
    search_engine: Literal['sqlite_fts5_bm25'] = 'sqlite_fts5_bm25'
    authority: Literal['project_memory'] = 'project_memory'
    raw_source_payloads_returned: bool = False
    query_mutated_storage: bool = False


class MemoryCheckpoint(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    plan_revision: int = Field(ge=1)
    contract_digest: str = Field(pattern=DIGEST)
    locator_ids: list[str] = Field(max_length=20)

    @model_validator(mode='after')
    def unique_ids(self):
        if len(set(self.locator_ids)) != len(self.locator_ids) or any(not re.fullmatch(DIGEST, key) for key in self.locator_ids):
            raise ValueError('Select distinct locator digests')
        return self


class MemoryCheckpointResult(Contract):
    checkpoint_digest: str
    project_id: str
    source_client_id: str
    source_task_binding: str | None
    native_task_attestation: Literal['not_provided'] = 'not_provided'
    host_session_attached: bool = False


class MemoryRehydrate(Contract):
    checkpoint_digest: str = Field(pattern=DIGEST)


class MemoryContinuity(Contract):
    project_id: str
    checkpoint: dict
    locators: list[dict]
    current_plan_revision: int | None
    plan_compatible: bool
    memory_head_changed: bool
    lineage_head_changed: bool
    receiver_client_id: str
    native_task_attestation: Literal['not_provided'] = 'not_provided'
    host_session_attached: bool = False
    source_authorities_mutated: bool = False


MEMORY_MIGRATIONS = (Migration('memory', 1, 'Bounded locators, typed edges, immutable events and checkpoint slices', (
    """CREATE TABLE memory_locators (locator_id TEXT PRIMARY KEY, reference_kind TEXT NOT NULL,
       reference_key TEXT NOT NULL, reference_digest TEXT NOT NULL, reference_revision INTEGER, profile TEXT,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)), row_digest TEXT NOT NULL,
       created_at TEXT NOT NULL)""",
    'CREATE INDEX memory_reference ON memory_locators(reference_kind,reference_key,reference_revision)',
    "CREATE VIRTUAL TABLE memory_fts USING fts5(locator_id UNINDEXED,label,text,tokenize='unicode61')",
    """CREATE TABLE memory_edges (edge_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES memory_locators(locator_id),
       target_id TEXT NOT NULL REFERENCES memory_locators(locator_id), kind TEXT NOT NULL,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)), row_digest TEXT NOT NULL, created_at TEXT NOT NULL)""",
    'CREATE INDEX memory_edge_target ON memory_edges(target_id,kind)',
    'CREATE INDEX memory_edge_source ON memory_edges(source_id,kind)',
    """CREATE TABLE memory_events (sequence INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
       kind TEXT NOT NULL, actor_id TEXT NOT NULL, input_digest TEXT NOT NULL,
       result_json TEXT NOT NULL CHECK(json_valid(result_json)), previous_digest TEXT,
       digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)""",
    """CREATE TABLE memory_checkpoints (checkpoint_digest TEXT PRIMARY KEY,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)))""",
)),)


def locator_identity(project_id, locator):
    return content_digest({'project_id': project_id, 'locator': locator.model_dump()})


class ProjectMemory:
    def __init__(self, store):
        self.project = store.project if isinstance(store, LaneStore) else store
        self.store = self.project.lane('memory')

    def initialize(self, lease):
        if lease.store.root != self.store.root or lease.store.project_id != self.store.project_id:
            raise LaneError('WRITER_PROJECT_MISMATCH', 'The Memory writer belongs to another project.')
        lease.check()
        apply_migrations(self.store, MEMORY_MIGRATIONS, writer=lease)

    @staticmethod
    def _table(connection, name):
        return connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (name,)).fetchone() is not None

    @staticmethod
    def _head(connection):
        row = connection.execute('SELECT digest FROM memory_events ORDER BY sequence DESC LIMIT 1').fetchone()
        return row[0] if row else None

    def _plan_head(self, connection):
        with self.project.lane('plan').connection(read_only=True) as plan:
            if not self._table(plan, 'plan_current'):
                return None
            row = plan.execute('SELECT revision FROM plan_current WHERE singleton=1').fetchone()
            return row[0] if row else None

    def _lineage_head(self, connection):
        with self.project.lane('chat_lineage').connection(read_only=True) as lineage:
            if not self._table(lineage, 'lineage_events'):
                return None
            row = lineage.execute('SELECT cursor FROM lineage_events ORDER BY sequence DESC LIMIT 1').fetchone()
            return row[0] if row else None

    def _reference(self, connection, ref):
        """Verify the reference against its owner; do not import source content."""
        lane_id = {'plan_task': 'plan', 'lineage_event': 'chat_lineage',
                   'learning_version': 'learning', 'receipt': 'receipts',
                   'canon_exchange': 'canon', 'source_object': ref.lane_id}[ref.kind]
        try:
            selected = self.project.lane(lane_id)
        except LaneError as error:
            if error.code == 'LANE_NOT_INITIALIZED':
                raise LaneError('MEMORY_SOURCE_MISMATCH', 'The referenced lane has no recorded source.') from None
            raise
        with selected.connection(read_only=True) as owner:
            return self._owned_reference(owner, ref)

    def _owned_reference(self, connection, ref):
        row, actual, state = None, None, 'recorded'
        if ref.kind == 'plan_task' and self._table(connection, 'plan_tasks'):
            row = connection.execute('SELECT * FROM plan_tasks WHERE revision=? AND task_id=?', (ref.revision, ref.key)).fetchone()
            if row:
                try:
                    actual = PlanStore._view(row, connection=connection).contract_digest
                except (LaneError, ValueError):
                    raise LaneError('MEMORY_SOURCE_INTEGRITY', 'The Plan contract or dependency identity is inconsistent.') from None
                state = row['state'] if ref.revision == self._plan_head(connection) else 'historical'
        elif ref.kind == 'lineage_event' and self._table(connection, 'lineage_events'):
            from .lineage import ChatLineage
            row = connection.execute('SELECT * FROM lineage_events WHERE event_id=?', (ref.key,)).fetchone()
            if row:
                ChatLineage.validate_row(row)
                actual = row['cursor']
        elif ref.kind == 'learning_version' and self._table(connection, 'learning_versions'):
            row = connection.execute('SELECT * FROM learning_versions WHERE version_id=?', (ref.key,)).fetchone()
            if row:
                actual, state = row['content_digest'], row['state']
                # Source remains a Learning observation, never a Memory-owned conclusion.
                if content_digest(json.loads(row['scope_json'])) != row['lesson_key']:
                    raise LaneError('MEMORY_SOURCE_INTEGRITY', 'The Learning scope identity is inconsistent.')
        elif ref.kind == 'receipt':
            row = connection.execute('SELECT * FROM receipts WHERE receipt_id=?', (ref.key,)).fetchone()
            if row:
                actual = content_digest(json.loads(row['body_json']))
        elif ref.kind == 'source_object':
            row = connection.execute('SELECT digest FROM objects WHERE digest=?', (ref.key,)).fetchone()
            if row:
                actual, state = row[0], 'registered_content_reference'
        elif ref.kind == 'canon_exchange' and self._table(connection, 'canon_exchanges'):
            from .canon_task_graph import CanonStore
            row, _ = CanonStore(self.store).exchange(connection, ref.key)
            actual, state = row['envelope_digest'], row['state']
        if row is None or actual != ref.digest:
            raise LaneError('MEMORY_SOURCE_MISMATCH', 'The reference must match an existing record in this project.')
        return {'source_state': state, 'source_content_returned': False,
                'source_bytes_reverified': False, 'profile_attribution': 'agent_report' if ref.profile else None}

    @staticmethod
    def _event(connection, request_id, kind, actor_id, input_digest, result):
        previous = connection.execute('SELECT sequence,digest FROM memory_events ORDER BY sequence DESC LIMIT 1').fetchone()
        body = {'sequence': previous['sequence'] + 1 if previous else 1, 'request_id': request_id,
                'kind': kind, 'actor_id': actor_id, 'input_digest': input_digest, 'result_json': json_text(result),
                'previous_digest': previous['digest'] if previous else None, 'created_at': now()}
        digest = content_digest(body)
        connection.execute('INSERT INTO memory_events VALUES(?,?,?,?,?,?,?,?,?)',
            (body['sequence'], request_id, kind, actor_id, input_digest, body['result_json'], body['previous_digest'], digest, body['created_at']))
        return digest

    @staticmethod
    def _replay(connection, request_id, kind, actor_id, input_digest):
        row = connection.execute('SELECT * FROM memory_events WHERE request_id=?', (request_id,)).fetchone()
        if row:
            if row['kind'] != kind or row['actor_id'] != actor_id or row['input_digest'] != input_digest:
                raise LaneError('MEMORY_REQUEST_CONFLICT', 'This request ID belongs to different Memory content or attribution.')
            value = dict(row)
            digest = value.pop('digest')
            if content_digest(value) != digest:
                raise LaneError('MEMORY_HISTORY_INTEGRITY', 'The recorded Memory event is inconsistent.')
            return row
        return None

    def ingest(self, request, lease, *, actor_id):
        return self._ingest(request, lease, actor_id=actor_id, operation='ingest')

    def record_link(self, request, lease, *, actor_id):
        request = MemoryRecordLink.model_validate(request.model_dump())
        edge = MemoryEdge(source_id=locator_identity(self.store.project_id, request.source),
            target_id=locator_identity(self.store.project_id, request.target),
            kind=request.edge_type, evidence=request.evidence)
        batch = MemoryIngest(request_id=request.request_id, locators=[request.source, request.target], edges=[edge])
        return self._ingest(batch, lease, actor_id=actor_id, operation='record_link')

    def _ingest(self, request, lease, *, actor_id, operation, transaction=None,
                provenance='agent_report'):
        request = MemoryIngest.model_validate(request.model_dump())
        self.initialize(lease)
        input_digest = content_digest(request.model_dump())
        with nullcontext(transaction) if transaction is not None else lease.transaction('memory') as connection:
            lease.check(connection)
            self.store.require_transaction(connection)
            previous = self._replay(connection, request.request_id, operation, actor_id, input_digest)
            if previous:
                return MemoryIngested(**json.loads(previous['result_json']), head=previous['digest'], duplicate=True)
            locator_ids, edge_ids = [], []
            for locator in request.locators:
                ref = locator.reference
                self._reference(connection, ref)
                identity = locator_identity(self.store.project_id, locator)
                locator_ids.append(identity)
                existing = connection.execute('SELECT * FROM memory_locators WHERE locator_id=?', (identity,)).fetchone()
                if existing:
                    self._locator(connection, existing)
                    continue
                stamp = now()
                body = {'project_id': self.store.project_id, 'locator': locator.model_dump(),
                        'source_client_id': actor_id, 'metadata_provenance': provenance, 'created_at': stamp}
                connection.execute('INSERT INTO memory_locators VALUES(?,?,?,?,?,?,?,?,?)',
                    (identity, ref.kind, ref.key, ref.digest, ref.revision, ref.profile, json_text(body), content_digest(body), stamp))
                connection.execute('INSERT INTO memory_fts VALUES(?,?,?)',
                    (identity, locator.label, ' '.join([ref.kind, ref.profile or '', *locator.search_terms])))
            for edge in request.edges:
                for key in (edge.source_id, edge.target_id):
                    row = connection.execute('SELECT * FROM memory_locators WHERE locator_id=?', (key,)).fetchone()
                    if row is None:
                        raise LaneError('MEMORY_EDGE_ENDPOINT_MISSING', 'Both endpoints must exist in this project and batch.')
                    self._locator(connection, row)
                self._reference(connection, edge.evidence)
                identity = content_digest({'project_id': self.store.project_id, 'edge': edge.model_dump()})
                edge_ids.append(identity)
                existing = connection.execute('SELECT * FROM memory_edges WHERE edge_id=?', (identity,)).fetchone()
                if existing:
                    self._edge(connection, existing)
                    continue
                stamp = now()
                body = {'project_id': self.store.project_id, 'edge': edge.model_dump(),
                        'source_client_id': actor_id, 'semantics_provenance': provenance, 'created_at': stamp}
                connection.execute('INSERT INTO memory_edges VALUES(?,?,?,?,?,?,?)',
                    (identity, edge.source_id, edge.target_id, edge.kind, json_text(body), content_digest(body), stamp))
            result = {'project_id': self.store.project_id, 'locator_ids': locator_ids, 'edge_ids': edge_ids}
            if provenance != 'agent_report':
                result['link_semantics'] = provenance
            head = self._event(connection, request.request_id, operation, actor_id, input_digest, result)
            receipt_kind = {'record_link': 'project_memory_record_link',
                            'verified_exit': 'delta_exit_memory_refreshed'}.get(operation, 'memory_ingest')
            self.store.append_receipt(receipt_kind, {**result, 'memory_head': head, 'source_client_id': actor_id}, connection=connection)
        return MemoryIngested(**result, head=head)

    def _verified_exit_batch(self, job_id, receipt_id):
        """Derive a fixed-size reference batch from the actual completion owners."""
        def require(condition):
            if not condition:
                raise LaneError('MEMORY_VERIFIED_EXIT_REQUIRED', 'Memory requires matching Plan, Receipt and Learning exit evidence.')

        with self.project.lane('receipts').connection(read_only=True) as receipts:
            row = receipts.execute("SELECT body_json FROM receipts WHERE receipt_id=? AND kind='delta_exit_verified' "
                "AND length(CAST(body_json AS BLOB))<=262144", (receipt_id,)).fetchone()
        require(row is not None)
        receipt = json.loads(row[0])
        require(receipt.get('project_id') == self.project.project_id and receipt.get('job_id') == job_id
            and receipt.get('status') == 'passed' and receipt.get('memory_refresh_policy') == 'verified_exit_memory_v1')
        with self.project.lane('plan').connection(read_only=True) as plan:
            row = plan.execute('SELECT x.receipt_id,x.verification_object,d.state,d.task_id,d.plan_revision,'
                'd.contract_digest,d.result_object FROM delta_exits x JOIN delta_runs d ON d.job_id=x.job_id '
                'WHERE x.job_id=?', (job_id,)).fetchone()
        require(row is not None and row['state'] == 'verified' and row['receipt_id'] == receipt_id
            and all(row[key] == receipt.get(key) for key in
                ('task_id', 'plan_revision', 'contract_digest', 'verification_object', 'result_object')))
        with self.project.lane('learning').connection(read_only=True) as learning:
            learned = learning.execute('SELECT * FROM learning_versions WHERE source_job_id=?', (job_id,)).fetchall()
        require(len(learned) == 1 and learned[0]['source_receipt_id'] == receipt_id)
        learned = learned[0]
        learning_store = self.project.lane('learning')
        path = learning_store.object_path(learned['content_digest'])
        require(path.is_file() and path.stat().st_size <= 65_536)
        observation = json.loads(learning_store.read_object(learned['content_digest']))
        scope = observation.get('scope', {})
        require(observation.get('project_id') == self.project.project_id and observation.get('source_job_id') == job_id
            and observation.get('source_task_id') == row['task_id'] and observation.get('plan_revision') == row['plan_revision']
            and observation.get('verification_object') == row['verification_object']
            and observation.get('result_object') == row['result_object']
            and observation.get('engine_instance') == receipt.get('engine_instance')
            and json_text(scope) == learned['scope_json'] and content_digest(scope) == learned['lesson_key']
            and scope.get('profile') == learned['profile'] and scope.get('action') == learned['action'])
        task_ref = MemoryReference(kind='plan_task', key=row['task_id'], revision=row['plan_revision'], digest=row['contract_digest'])
        receipt_ref = MemoryReference(kind='receipt', key=receipt_id, digest=content_digest(receipt))
        learning_ref = MemoryReference(kind='learning_version', key=learned['version_id'], digest=learned['content_digest'])
        result_ref = MemoryReference(kind='source_object', key=row['result_object'], digest=row['result_object'],
            profile=learned['profile'], lane_id='plan')
        # Labels deliberately contain no prompt, source payload, path or check output.
        locators = [MemoryLocator(reference=ref, label=label, search_terms=['verified', 'completion'])
            for ref, label in ((task_ref, 'Verified Plan task'), (receipt_ref, 'Verified Delta exit receipt'),
                (learning_ref, 'Learning observation from verified exit'), (result_ref, 'Addressed Delta result'))]
        identities = [locator_identity(self.project.project_id, locator) for locator in locators]
        edges = [MemoryEdge(source_id=identities[source], target_id=identities[target], kind=kind, evidence=receipt_ref)
            for source, target, kind in ((1, 0, 'EVIDENCES'), (2, 1, 'LEARNED_FROM'), (2, 3, 'DERIVED_FROM'))]
        return MemoryIngest(request_id=str(uuid5(UUID(self.project.project_id), 'verified-exit:' + job_id)),
            locators=locators, edges=edges)

    def record_verified_exit(self, job_id, receipt_id, lease, *, transaction):
        """Index this exit atomically; this is an engine hook, not a public write action."""
        lease.check(transaction)
        self.store.require_transaction(transaction)
        request = self._verified_exit_batch(job_id, receipt_id)
        result = self._ingest(request, lease, actor_id='engine_verified_exit', operation='verified_exit',
            transaction=transaction, provenance='engine_verified_exit')
        return self.read_verified_exit(job_id, receipt_id).model_copy(update={'duplicate': result.duplicate})

    def read_verified_exit(self, job_id, receipt_id):
        """Verify the bounded recorded reference batch without refreshing it."""
        request = self._verified_exit_batch(job_id, receipt_id)
        with self.store.connection(read_only=True) as connection:
            row = self._replay(connection, request.request_id, 'verified_exit', 'engine_verified_exit',
                               content_digest(request.model_dump())) if self._table(connection, 'memory_events') else None
            if row is None:
                raise LaneError('MEMORY_EXIT_INTEGRITY', 'The declared verified-exit Memory refresh is missing.')
            result = MemoryIngested(**json.loads(row['result_json']), head=row['digest'])
            expected_locators = [locator_identity(self.project.project_id, locator) for locator in request.locators]
            expected_edges = [content_digest({'project_id': self.project.project_id, 'edge': edge.model_dump()})
                for edge in request.edges]
            if (result.project_id != self.project.project_id or result.locator_ids != expected_locators
                    or result.edge_ids != expected_edges or result.link_semantics != 'engine_verified_exit'):
                raise LaneError('MEMORY_EXIT_INTEGRITY', 'The recorded Memory refresh differs from its completion evidence.')
            for table, column, identities, reader in (
                    ('memory_locators', 'locator_id', expected_locators, self._locator),
                    ('memory_edges', 'edge_id', expected_edges, self._edge)):
                for identity in identities:
                    member = connection.execute(f'SELECT * FROM {table} WHERE {column}=?', (identity,)).fetchone()
                    if member is None:
                        raise LaneError('MEMORY_EXIT_INTEGRITY', 'A verified-exit Memory reference is missing.')
                    reader(connection, member)
            with self.project.lane('receipts').connection(read_only=True) as receipts:
                receipts_rows = receipts.execute("SELECT body_json FROM receipts WHERE kind='delta_exit_memory_refreshed' "
                    "AND json_extract(body_json,'$.memory_head')=? LIMIT 2", (result.head,)).fetchall()
            expected_receipt = {key: value for key, value in result.model_dump().items()
                if key in {'project_id', 'locator_ids', 'edge_ids', 'link_semantics'}}
            expected_receipt.update(memory_head=result.head, source_client_id='engine_verified_exit', owner_lane='memory')
            if (len(receipts_rows) != 1 or len(receipts_rows[0][0].encode()) > 65_536
                    or json.loads(receipts_rows[0][0]) != expected_receipt):
                raise LaneError('MEMORY_EXIT_INTEGRITY', 'The Memory refresh receipt differs from the recorded event.')
        return result

    def _locator(self, connection, row):
        body = json.loads(row['body_json'])
        locator = MemoryLocator.model_validate(body.get('locator'))
        ref = locator.reference
        if (content_digest(body) != row['row_digest'] or body.get('project_id') != self.store.project_id
                or locator_identity(self.store.project_id, locator) != row['locator_id']
                or tuple(row[key] for key in ('reference_kind', 'reference_key', 'reference_digest', 'reference_revision', 'profile'))
                   != (ref.kind, ref.key, ref.digest, ref.revision, ref.profile) or body.get('created_at') != row['created_at']):
            raise LaneError('MEMORY_LOCATOR_INTEGRITY', 'A stored Memory locator differs from its bounded identity.')
        source = self._reference(connection, ref)
        if ref.profile and body.get('metadata_provenance') == 'engine_verified_exit':
            source['profile_attribution'] = 'engine_verified_exit'
        return {'locator_id': row['locator_id'], **body, **source}

    def _edge(self, connection, row):
        body = json.loads(row['body_json'])
        edge = MemoryEdge.model_validate(body.get('edge'))
        if (content_digest(body) != row['row_digest'] or body.get('project_id') != self.store.project_id
                or content_digest({'project_id': self.store.project_id, 'edge': edge.model_dump()}) != row['edge_id']
                or (row['source_id'], row['target_id'], row['kind']) != (edge.source_id, edge.target_id, edge.kind)):
            raise LaneError('MEMORY_EDGE_INTEGRITY', 'A stored Memory edge differs from its identity.')
        self._reference(connection, edge.evidence)
        return {'edge_id': row['edge_id'], **body}

    def _item(self, connection, row):
        item = self._locator(connection, row)
        edges = connection.execute('SELECT * FROM memory_edges WHERE source_id=? OR target_id=? ORDER BY created_at,edge_id LIMIT 9',
                                   (row['locator_id'], row['locator_id'])).fetchall()
        item['edges'] = [self._edge(connection, edge) for edge in edges[:8]]
        item['edges_truncated'] = len(edges) > 8
        item['suppressed'] = connection.execute("SELECT 1 FROM memory_edges WHERE target_id=? AND kind IN ('REVOKES','SUPERSEDES','SUPPRESSES') LIMIT 1",
                                               (row['locator_id'],)).fetchone() is not None
        return item

    def read(self, request=None):
        request = request or MemoryRead()
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            if not self._table(connection, 'memory_locators'):
                return MemoryPage(project_id=self.store.project_id, locators=[], head=None, truncated=False)
            deadline = time.monotonic() + 5
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            conditions, parameters, join, order = ['1=1'], [], '', 'm.created_at DESC,m.locator_id'
            if request.query:
                terms = sorted(set(re.findall(r'\w+', request.query, re.UNICODE)))[:16]
                if not terms:
                    raise LaneError('MEMORY_QUERY_TERMS_REQUIRED', 'Use a query with indexable terms.')
                join = ' JOIN memory_fts f ON f.locator_id=m.locator_id'
                conditions.append('memory_fts MATCH ?')
                parameters.append(' OR '.join('"' + term + '"' for term in terms))
                order = 'bm25(memory_fts,0,5,2),m.locator_id'
            if request.kinds:
                conditions.append('m.reference_kind IN (' + ','.join('?' for _ in request.kinds) + ')')
                parameters.extend(request.kinds)
            if request.profile:
                conditions.append('m.profile=?')
                parameters.append(request.profile)
            if not request.include_history:
                # Local scope and suppression stay indexed. Cross-authority
                # state is checked before the result limit under the same Root
                # PV pin, with an explicit candidate/time bound.
                conditions.append("NOT EXISTS(SELECT 1 FROM memory_edges e WHERE e.target_id=m.locator_id AND e.kind IN ('REVOKES','SUPERSEDES','SUPPRESSES'))")
                conditions.append("(m.reference_kind!='plan_task' OR m.reference_revision=?)")
                parameters.append(self._plan_head(connection))
            rows = connection.execute('SELECT m.* FROM memory_locators m' + join + ' WHERE ' + ' AND '.join(conditions)
                                      + ' ORDER BY ' + order + ' LIMIT ?', [*parameters, 1025]).fetchall()
            selected, size = [], 0
            truncated = len(rows) > 1024
            for row in rows[:1024]:
                if time.monotonic() >= deadline:
                    truncated = True
                    break
                item = self._item(connection, row)
                if not request.include_history and (
                    row['reference_kind'] == 'learning_version' and item['source_state'] != 'active'
                    or row['reference_kind'] == 'canon_exchange' and item['source_state'] in {'rejected', 'superseded'}
                ):
                    continue
                size += len(json_text(item).encode())
                if len(selected) == request.limit or size > request.max_bytes:
                    truncated = True
                    break
                selected.append(item)
            return MemoryPage(project_id=self.store.project_id, locators=selected, head=self._head(connection), truncated=truncated)

    def checkpoint(self, request, lease, *, actor_id, source_task_binding=None):
        self.initialize(lease)
        input_digest = content_digest({'request': request.model_dump(), 'source_task_binding': source_task_binding})
        with lease.transaction('memory') as connection:
            previous = self._replay(connection, request.request_id, 'checkpoint', actor_id, input_digest)
            if previous:
                return MemoryCheckpointResult(**json.loads(previous['result_json']))
            task_ref = MemoryReference(kind='plan_task', key=request.task_id, revision=request.plan_revision, digest=request.contract_digest)
            self._reference(connection, task_ref)
            if request.plan_revision != self._plan_head(connection):
                raise LaneError('MEMORY_CHECKPOINT_STALE_PLAN', 'A new checkpoint must bind the current Plan revision.')
            locators = []
            for key in request.locator_ids:
                row = connection.execute('SELECT * FROM memory_locators WHERE locator_id=?', (key,)).fetchone()
                if row is None:
                    raise LaneError('MEMORY_CHECKPOINT_LOCATOR_MISSING', 'Select locators from this project.')
                self._locator(connection, row)
                locators.append({'locator_id': key, 'row_digest': row['row_digest']})
            body = {'project_id': self.store.project_id, 'source_client_id': actor_id, 'source_task_binding': source_task_binding,
                    'task': task_ref.model_dump(), 'memory_head': self._head(connection), 'lineage_head': self._lineage_head(connection),
                    'locators': locators, 'created_at': now(), 'native_task_attestation': 'not_provided', 'host_session_attached': False}
            digest = content_digest(body)
            connection.execute('INSERT INTO memory_checkpoints VALUES(?,?)', (digest, json_text(body)))
            result = MemoryCheckpointResult(checkpoint_digest=digest, project_id=self.store.project_id,
                source_client_id=actor_id, source_task_binding=source_task_binding)
            self._event(connection, request.request_id, 'checkpoint', actor_id, input_digest, result.model_dump())
            return result

    def rehydrate(self, request, *, receiver_client_id):
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            row = connection.execute('SELECT * FROM memory_checkpoints WHERE checkpoint_digest=?', (request.checkpoint_digest,)).fetchone() if self._table(connection, 'memory_checkpoints') else None
            if row is None:
                raise LaneError('MEMORY_CHECKPOINT_NOT_FOUND', 'The checkpoint is not stored in this project.')
            body = json.loads(row['body_json'])
            if content_digest(body) != request.checkpoint_digest or body.get('project_id') != self.store.project_id:
                raise LaneError('MEMORY_CHECKPOINT_INTEGRITY', 'The checkpoint content differs from its identity.')
            task = MemoryReference.model_validate(body['task'])
            self._reference(connection, task)
            locators = []
            for item in body['locators']:
                row = connection.execute('SELECT * FROM memory_locators WHERE locator_id=?', (item['locator_id'],)).fetchone()
                if row is None or row['row_digest'] != item['row_digest']:
                    raise LaneError('MEMORY_CHECKPOINT_INTEGRITY', 'The checkpoint locator slice is inconsistent.')
                locators.append(self._locator(connection, row))
            # Ignore checkpoint-only events when comparing indexed Memory content.
            sealed = connection.execute('SELECT sequence FROM memory_events WHERE digest=?', (body['memory_head'],)).fetchone()
            changed = bool(connection.execute("SELECT 1 FROM memory_events WHERE kind IN ('ingest','record_link','verified_exit') AND sequence>? LIMIT 1", (sealed[0] if sealed else 0,)).fetchone())
            current = self._plan_head(connection)
            return MemoryContinuity(project_id=self.store.project_id, checkpoint=body, locators=locators,
                current_plan_revision=current, plan_compatible=current == task.revision, memory_head_changed=changed,
                lineage_head_changed=self._lineage_head(connection) != body['lineage_head'], receiver_client_id=receiver_client_id)

    def verify_history(self, *, limit=50_000):
        if not 1 <= limit <= 50_000:
            raise LaneError('MEMORY_HISTORY_BUDGET', 'Select a bounded history verification limit.')
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            rows = connection.execute('SELECT * FROM memory_events ORDER BY sequence LIMIT ?', (limit + 1,)).fetchall() if self._table(connection, 'memory_events') else []
            if len(rows) > limit:
                raise LaneError('MEMORY_HISTORY_BUDGET', 'The Memory history exceeds the selected budget.')
            previous = None
            for sequence, row in enumerate(rows, 1):
                body = dict(row)
                digest = body.pop('digest')
                if row['sequence'] != sequence or row['previous_digest'] != previous or content_digest(body) != digest:
                    raise LaneError('MEMORY_HISTORY_INTEGRITY', 'The Memory event chain is inconsistent.')
                previous = digest
            return {'events_verified': len(rows), 'head': previous}


def memory_view(store, scope):
    from .lane_contract import ViewGraph
    page = ProjectMemory(store).read(MemoryRead(limit=min(scope.node_limit, 20), query=scope.query,
                                              include_history=scope.include_history))
    graph = ViewGraph(store.project_id, scope)
    ids = {}
    for item in page.locators:
        ids[item['locator_id']] = graph.node('memory_locator', item['locator_id'], item['locator']['label'],
            state='suppressed' if item['suppressed'] else item['source_state'],
            locator={'reference': item['locator']['reference'], 'source_client_id': item['source_client_id']})
    for item in page.locators:
        for edge in item['edges']:
            graph.edge(ids.get(edge['edge']['source_id']), ids.get(edge['edge']['target_id']), edge['edge']['kind'],
                       provenance=edge['semantics_provenance'], evidence=edge['edge']['evidence'])
        graph.truncated |= item['edges_truncated']
    graph.truncated |= page.truncated
    return graph.result()


def register_memory_actions(engine):
    from .agent_learning import LEARNING_MIGRATIONS
    from .canon_task_graph import CANON_MIGRATIONS
    from .lineage import LINEAGE_MIGRATIONS
    from .plan_runtime import PLAN_MIGRATIONS
    read_schemas = (*MEMORY_MIGRATIONS, *PLAN_MIGRATIONS, *LINEAGE_MIGRATIONS, *LEARNING_MIGRATIONS, *CANON_MIGRATIONS)
    def ingest(context, request):
        store = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(store) as lease:
            return ProjectMemory(store).ingest(request, lease, actor_id=context.client_id)

    def record_link(context, request):
        store = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(store) as lease:
            return ProjectMemory(store).record_link(request, lease, actor_id=context.client_id)

    def checkpoint(context, request):
        store = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(store) as lease:
            return ProjectMemory(store).checkpoint(request, lease, actor_id=context.client_id, source_task_binding=context.native_task_id)

    engine.registry.register(ActionSpec('memory_ingest', 'Index bounded attributed locators and typed links to existing project records.',
        MemoryIngest, MemoryIngested, ingest, permission='write', profile='memory', mutates=True, workflow='memory'))
    engine.registry.register(ActionSpec('project_memory_record_link',
        'Record two locators and one evidence-backed Project Memory link atomically; derive endpoint identities inside the engine.',
        MemoryRecordLink, MemoryIngested, record_link, permission='write', profile='memory', mutates=True, workflow='memory'))
    engine.registry.register(ActionSpec('memory_read', 'Search a bounded project-local locator slice without loading source payloads.',
        MemoryRead, MemoryPage, lambda context, request: ProjectMemory(engine.directory.open(context.project_id)).read(request),
        profile='memory', queryable_in_delta=True, cross_project_read=True, read_migrations=read_schemas, workflow='memory',
        search=SearchRoute(('memory',), 'locators')))
    engine.registry.register(ActionSpec('memory_checkpoint', 'Pin a bounded Memory slice to current Plan and attributed visible lineage.',
        MemoryCheckpoint, MemoryCheckpointResult, checkpoint, permission='write', profile='memory', mutates=True, workflow='memory'))
    engine.registry.register(ActionSpec('memory_rehydrate', 'Read a checkpoint slice with explicit attribution and current compatibility.',
        MemoryRehydrate, MemoryContinuity, lambda context, request: ProjectMemory(engine.directory.open(context.project_id)).rehydrate(request, receiver_client_id=context.client_id),
        profile='memory', queryable_in_delta=True, workflow='memory'))
