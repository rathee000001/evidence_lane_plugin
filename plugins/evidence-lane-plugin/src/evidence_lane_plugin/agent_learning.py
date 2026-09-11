"""Project-local Learning from verified Delta observations, with explicit history.

Retains evidence binding, scoped FTS, append-only events and revocation from
this module's earlier implementation. Its lane database owns Learning separately
from Plan, task exchange authority, Project Memory and visible ChatLineage. There is no HIL or
PV pointer. Observed checks are not generalized into untested causal advice.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import Field, model_validator

from .errors import LaneError
from .migrations import Migration, apply_migrations
from .plan_runtime import TASK_ID_PATTERN, PlanStore, content_digest
from .redaction import contains_secret, redact
from .registry import ActionSpec, Contract, SearchRoute
from .sdk import UUID_PATTERN
from .storage import LaneStore, json_text, now

LEARNING_MIGRATIONS = (Migration('learning', 1, 'Scoped verified observations, current versions and revocation history', (
    """CREATE TABLE learning_versions (
       version_id TEXT PRIMARY KEY, lesson_key TEXT NOT NULL, version INTEGER NOT NULL,
       profile TEXT NOT NULL, action TEXT NOT NULL, scope_json TEXT NOT NULL CHECK(json_valid(scope_json)),
       content_digest TEXT NOT NULL REFERENCES objects(digest),
       source_job_id TEXT NOT NULL UNIQUE,
       source_receipt_id TEXT NOT NULL,
       state TEXT NOT NULL CHECK(state IN ('active','superseded','revoked')), created_at TEXT NOT NULL,
       UNIQUE(lesson_key,version))""",
    'CREATE INDEX learning_scope ON learning_versions(profile,action,state)',
    """CREATE TABLE learning_current (lesson_key TEXT PRIMARY KEY,
       version_id TEXT NOT NULL UNIQUE REFERENCES learning_versions(version_id))""",
    """CREATE TABLE learning_controls (lesson_key TEXT PRIMARY KEY,
       revoked_by TEXT NOT NULL, reason TEXT NOT NULL, revoked_at TEXT NOT NULL)""",
    """CREATE TABLE learning_events (sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
       version_id TEXT NOT NULL REFERENCES learning_versions(version_id), kind TEXT NOT NULL,
       actor_id TEXT NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json)),
       previous_digest TEXT, digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)""",
    "CREATE VIRTUAL TABLE learning_fts USING fts5(version_id UNINDEXED,text,tokenize='unicode61')",
)),)


HOST_MEMORY_MIGRATIONS = (Migration('hostmemory', 1, 'Explicit host-memory provenance receipt and Memory-link identities', (
    """CREATE TABLE hostmemory_imports (
       request_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, input_digest TEXT NOT NULL,
       receipt_id TEXT NOT NULL UNIQUE REFERENCES receipts(receipt_id), receipt_digest TEXT NOT NULL,
       result_json TEXT NOT NULL CHECK(json_valid(result_json)), result_digest TEXT NOT NULL)""",
)),)
_HOST_MEMORY_SCHEMES = {'CODEX_LOCAL_MEMORY': 'codex-local-memory',
    'CHATGPT_SAVED_MEMORY': 'chatgpt-memory', 'CHATGPT_CHAT_HISTORY_MEMORY': 'chatgpt-memory'}


class HostMemoryImport(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    source_kind: Literal['CODEX_LOCAL_MEMORY', 'CHATGPT_SAVED_MEMORY', 'CHATGPT_CHAT_HISTORY_MEMORY']
    source_locator: str = Field(min_length=1, max_length=512)
    source_record_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_context_id_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    observed_at: str = Field(min_length=20, max_length=40)
    purpose: str = Field(min_length=1, max_length=500)
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    plan_revision: int = Field(ge=1)
    contract_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    delta_job_id: str | None = Field(default=None, pattern=UUID_PATTERN)

    @model_validator(mode='after')
    def bounded_provenance(self):
        locator = urlsplit(self.source_locator)
        if (locator.scheme != _HOST_MEMORY_SCHEMES[self.source_kind]
                or not self.source_locator.startswith(locator.scheme + '://')
                or not (locator.netloc or locator.path) or locator.username or locator.password
                or locator.query or locator.fragment
                or any(ord(char) < 32 for char in self.source_locator)
                or not self.purpose.strip() or contains_secret(self.model_dump(mode='json'))):
            raise ValueError('Use bounded non-secret source provenance and the matching host-memory locator scheme')
        stamp = datetime.fromisoformat(self.observed_at)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError('The reported observation needs an explicit UTC offset')
        return self


class HostMemoryImported(Contract):
    project_id: str
    receipt_id: str
    receipt_digest: str
    source: dict
    import_context: dict
    memory_graph: dict
    duplicate: bool = False
    imported_role: Literal['non_authoritative_evidence_reference'] = 'non_authoritative_evidence_reference'
    source_identity_basis: Literal['caller_reported_hashes'] = 'caller_reported_hashes'
    memory_link_semantics: Literal['agent_report'] = 'agent_report'
    raw_host_memory_stored: Literal[False] = False
    source_bytes_read_or_verified: Literal[False] = False
    learning_mutated: Literal[False] = False
    plan_mutated: Literal[False] = False
    native_task_attestation: Literal['not_provided'] = 'not_provided'


def record_host_memory_import(project, request, lease, *, actor_id):
    """Record only explicit provenance; retain this original action's owner.

    Receipts owns the import; Project Memory owns its typed task link. No host
    memory content is read, imported, or promoted to a Learning observation.
    """
    from .project_memory import (
        MemoryEdge,
        MemoryIngest,
        MemoryLocator,
        MemoryReference,
        ProjectMemory,
        locator_identity,
    )

    request = HostMemoryImport.model_validate(request.model_dump())
    source = {'kind': request.source_kind, 'locator': request.source_locator,
        'locator_sha256': hashlib.sha256(request.source_locator.encode()).hexdigest(), 'record_sha256': request.source_record_sha256,
        'source_context_id_sha256': request.source_context_id_sha256,
        'observed_at': datetime.fromisoformat(request.observed_at).astimezone(UTC).isoformat(),
        'identity_basis': 'caller_reported_hashes'}
    input_digest = content_digest(request.model_dump(mode='json'))
    receipts, memory = project.lane('receipts'), ProjectMemory(project)
    with lease.coordinated_transaction(['receipts', 'memory']) as transaction:
        apply_migrations(receipts, HOST_MEMORY_MIGRATIONS, writer=lease)
        connection = transaction.connection('receipts')
        prior = connection.execute('SELECT * FROM hostmemory_imports WHERE request_id=?', (request.request_id,)).fetchone()
        if prior:
            if prior['actor_id'] != actor_id or prior['input_digest'] != input_digest:
                raise LaneError('HOST_MEMORY_REQUEST_CONFLICT', 'This request belongs to different provenance or an authenticated client.')
            result = HostMemoryImported.model_validate_json(prior['result_json'])
            receipt = connection.execute("SELECT * FROM receipts WHERE receipt_id=? AND kind='host_memory_import'", (prior['receipt_id'],)).fetchone()
            if (content_digest(result.model_dump()) != prior['result_digest'] or receipt is None
                    or content_digest(json.loads(receipt['body_json'])) != prior['receipt_digest']
                    or result.receipt_id != prior['receipt_id'] or result.receipt_digest != prior['receipt_digest']):
                raise LaneError('HOST_MEMORY_IMPORT_INTEGRITY', 'The saved import result differs from its provenance receipt.')
            # Revalidate referenced owners even when returning a historical result.
            memory_connection = transaction.connection('memory')
            for key in result.memory_graph['locator_ids']:
                row = memory_connection.execute('SELECT * FROM memory_locators WHERE locator_id=?', (key,)).fetchone()
                if row is None:
                    raise LaneError('HOST_MEMORY_IMPORT_INTEGRITY', 'The saved Memory locator is missing.')
                memory._locator(memory_connection, row)
            for key in result.memory_graph['edge_ids']:
                row = memory_connection.execute('SELECT * FROM memory_edges WHERE edge_id=?', (key,)).fetchone()
                if row is None:
                    raise LaneError('HOST_MEMORY_IMPORT_INTEGRITY', 'The saved Memory link is missing.')
                memory._edge(memory_connection, row)
            return result.model_copy(update={'duplicate': True})
        if datetime.fromisoformat(request.observed_at) > datetime.now(UTC):
            raise LaneError('HOST_MEMORY_IMPORT_TIME_INVALID', 'The reported observation cannot follow the engine import time.')
        task = PlanStore(project).task(request.task_id, expected_revision=request.plan_revision)
        if task.contract_digest != request.contract_digest:
            raise LaneError('HOST_MEMORY_PLAN_CONTRACT_CHANGED', 'Select the exact current Plan task contract.')
        if request.delta_job_id:
            with project.lane('plan').connection(read_only=True) as plan:
                row = plan.execute('SELECT * FROM delta_runs WHERE job_id=?', (request.delta_job_id,)).fetchone() if memory._table(plan, 'delta_runs') else None
                if (row is None or (row['task_id'], row['plan_revision'], row['contract_digest'])
                        != (request.task_id, request.plan_revision, request.contract_digest)):
                    raise LaneError('HOST_MEMORY_DELTA_MISMATCH', 'The referenced Delta must belong to this exact project task and contract.')
        import_context = {'task_id': request.task_id, 'plan_revision': request.plan_revision,
            'contract_digest': request.contract_digest, 'delta_job_id': request.delta_job_id,
            'imported_at': now(), 'imported_by': actor_id, 'purpose': request.purpose,
            'root_pv_before_import_commit': project.pv_head(), 'attribution': 'authenticated_engine_client'}
        body = {'schema': 'evidence-lane.host-memory-import.v4', 'project_id': project.project_id,
            'request_id': request.request_id, 'source': source, 'import_context': import_context,
            'imported_role': 'non_authoritative_evidence_reference', 'source_bytes_read_or_verified': False,
            'raw_host_memory_stored': False, 'learning_mutated': False, 'plan_mutated': False,
            'native_task_attestation': 'not_provided'}
        receipt_id = receipts.append_receipt('host_memory_import', body, connection=connection)
        receipt_digest = content_digest(body)
        reference = MemoryReference(kind='receipt', key=receipt_id, digest=receipt_digest)
        evidence = MemoryLocator(reference=reference, label='Explicit host-memory provenance',
            search_terms=['host', 'memory', 'provenance', request.source_kind.lower(), request.task_id])
        target = MemoryLocator(reference=MemoryReference(kind='plan_task', key=request.task_id,
            revision=request.plan_revision, digest=request.contract_digest), label='Plan task ' + request.task_id,
            search_terms=['plan', 'task', request.task_id])
        edge = MemoryEdge(source_id=locator_identity(project.project_id, evidence),
            target_id=locator_identity(project.project_id, target), kind='EVIDENCES', evidence=reference)
        graph = memory.ingest(MemoryIngest(request_id=request.request_id, locators=[evidence, target], edges=[edge]),
            lease, actor_id=actor_id)
        result = HostMemoryImported(project_id=project.project_id, receipt_id=receipt_id, receipt_digest=receipt_digest,
            source=source, import_context=import_context, memory_graph=graph.model_dump())
        connection.execute('INSERT INTO hostmemory_imports VALUES(?,?,?,?,?,?,?)',
            (request.request_id, actor_id, input_digest, receipt_id, receipt_digest,
             result.model_dump_json(), content_digest(result.model_dump())))
        return result


class LearningRead(Contract):
    query: str | None = Field(default=None, min_length=1, max_length=500)
    profile: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_]{0,63}$')
    action: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_]{0,63}$')
    limit: int = Field(default=8, ge=1, le=20)
    include_history: bool = False


class LearningPage(Contract):
    project_id: str
    lessons: list[dict]
    truncated: bool
    authority: str = 'project_learning'
    basis: str = 'verified_execution_observations_not_generalized_causal_claims'
    project_truth_mutated: bool = False


class LearningRevoke(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    version_id: str = Field(pattern=UUID_PATTERN)
    expected_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=1, max_length=1000)


class LearningRevoked(Contract):
    version_id: str
    lesson_key: str
    state: str = 'revoked'
    automatic_reactivation: bool = False


@dataclass(frozen=True)
class PreparedLearning:
    lesson_key: str
    profile: str
    action: str
    scope_json: str
    content_digest: str
    search_text: str
    source_job_id: str


class LearningStore:
    def __init__(self, store):
        self.project = store.project if isinstance(store, LaneStore) else store
        self.store = self.project.lane('learning')

    def initialize(self, lease):
        if lease.store.root != self.store.root or lease.store.project_id != self.store.project_id:
            raise LaneError('WRITER_PROJECT_MISMATCH', 'The Learning writer belongs to another project.')
        lease.check()
        apply_migrations(self.store, LEARNING_MIGRATIONS, writer=lease)

    def prepare(self, task, spec, execution, verification_object, result_object):
        scope = redact({'profile': spec.profile, 'action': spec.name,
                 'permitted_paths': sorted(task.permitted_paths), 'required_tools': sorted(spec.required_tools),
                 'worker_operations': sorted(spec.worker_operations), 'checks': sorted(task.acceptance_checks)})
        summary = f"Observed {spec.name} in {spec.profile}: the declared checks passed for one scoped execution."
        body = {'project_id': self.store.project_id, 'scope': scope, 'summary': summary,
                'source_job_id': execution.claim.job_id, 'source_task_id': task.task_id,
                'plan_revision': execution.plan_revision, 'verification_object': verification_object,
                'result_object': result_object, 'registry_schema_digest': content_digest(spec.schema()),
                'engine_instance': execution.coordinator.engine.instance_id,
                'inference_boundary': 'Observed checks in this scope; no claim of universal correctness or causal advantage.',
                'authority': 'project_learning'}
        body = redact(body)
        digest = self.store.put_object(json_text(body).encode(), limit=65_536)
        return PreparedLearning(content_digest(scope), spec.profile, spec.name, json_text(scope), digest,
                                summary + ' ' + ' '.join(task.acceptance_checks), execution.claim.job_id)

    @staticmethod
    def _event(connection, version_id, kind, actor_id, body, event_id=None):
        last = connection.execute('SELECT sequence,digest FROM learning_events ORDER BY sequence DESC LIMIT 1').fetchone()
        sequence, previous = (last['sequence'] + 1, last['digest']) if last else (1, None)
        body_json, stamp, identity = json_text(body), now(), event_id or str(uuid4())
        document = {'sequence': sequence, 'event_id': identity, 'version_id': version_id, 'kind': kind,
                    'actor_id': actor_id, 'body_json': body_json, 'previous_digest': previous, 'created_at': stamp}
        digest = content_digest(document)
        connection.execute('INSERT INTO learning_events VALUES(?,?,?,?,?,?,?,?,?)',
                           (sequence, identity, version_id, kind, actor_id, body_json, previous, digest, stamp))

    def record_exit(self, prepared, receipt_id, lease, *, transaction):
        lease.check(transaction)
        if lease.store.root != self.store.root or lease.store.project_id != self.store.project_id:
            raise LaneError('WRITER_PROJECT_MISMATCH', 'The Learning writer belongs to another project.')
        connection = transaction
        self.store.require_transaction(connection)
        with self.project.lane('receipts').connection(read_only=True) as receipts:
            receipt = receipts.execute("SELECT body_json FROM receipts WHERE receipt_id=? AND kind='delta_exit_verified'", (receipt_id,)).fetchone()
        body = json.loads(receipt[0]) if receipt else {}
        if (body.get('status') != 'passed' or body.get('job_id') != prepared.source_job_id
                or body.get('project_id') != self.store.project_id):
            raise LaneError('LEARNING_VERIFIED_EXIT_REQUIRED', 'Learning requires this project and job verified exit receipt.')
        with self.project.lane('plan').connection(read_only=True) as plan:
            exit_row = plan.execute('SELECT receipt_id,verification_object FROM delta_exits WHERE job_id=?',
                                    (prepared.source_job_id,)).fetchone()
        if exit_row is None or exit_row['receipt_id'] != receipt_id or exit_row['verification_object'] != body.get('verification_object'):
            raise LaneError('LEARNING_VERIFIED_EXIT_REQUIRED', 'Learning requires the matching Plan-owned Delta exit.')
        existing = connection.execute('SELECT * FROM learning_versions WHERE source_job_id=?', (prepared.source_job_id,)).fetchone()
        if existing:
            if existing['content_digest'] != prepared.content_digest or existing['source_receipt_id'] != receipt_id:
                raise LaneError('LEARNING_REPLAY_MISMATCH', 'This execution already has different Learning evidence.')
            return existing['version_id']
        previous = connection.execute('SELECT v.* FROM learning_current c JOIN learning_versions v ON v.version_id=c.version_id '
                                      'WHERE c.lesson_key=?', (prepared.lesson_key,)).fetchone()
        suppressed = connection.execute('SELECT 1 FROM learning_controls WHERE lesson_key=?', (prepared.lesson_key,)).fetchone()
        state = 'revoked' if suppressed else 'active'
        version_id, stamp = str(uuid4()), now()
        version = previous['version'] + 1 if previous else 1
        if previous and previous['state'] == 'active':
            connection.execute("UPDATE learning_versions SET state='superseded' WHERE version_id=?", (previous['version_id'],))
            self._event(connection, previous['version_id'], 'superseded', 'engine_verified_exit', {'successor_version_id': version_id})
        connection.execute('INSERT INTO learning_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (version_id, prepared.lesson_key, version, prepared.profile, prepared.action, prepared.scope_json,
             prepared.content_digest, prepared.source_job_id, receipt_id, state, stamp))
        connection.execute('INSERT INTO learning_current VALUES(?,?) ON CONFLICT(lesson_key) DO UPDATE SET version_id=excluded.version_id',
                           (prepared.lesson_key, version_id))
        connection.execute('INSERT INTO learning_fts VALUES(?,?)', (version_id, prepared.search_text))
        self._event(connection, version_id, 'observed_but_revoked' if suppressed else 'auto_observed', 'engine_verified_exit',
                    {'source_receipt_id': receipt_id, 'content_digest': prepared.content_digest, 'prior_version_id': previous['version_id'] if previous else None})
        return version_id

    def read(self, request=None, *, exact_scope_paths=None):
        request = request or LearningRead()
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='learning_versions'").fetchone():
                return LearningPage(project_id=self.store.project_id, lessons=[], truncated=False)
            conditions, parameters = ["1=1" if request.include_history else "v.state='active'"], []
            for field in ('profile', 'action'):
                value = getattr(request, field)
                if value:
                    conditions.append('v.' + field + '=?')
                    parameters.append(value)
            join, order = '', 'v.created_at DESC,v.version_id'
            if request.query:
                terms = sorted(set(re.findall(r'\w+', request.query, flags=re.UNICODE)))[:16]
                if not terms:
                    raise LaneError('LEARNING_QUERY_TERMS_REQUIRED', 'Use a query with indexable terms.')
                join = ' JOIN learning_fts f ON f.version_id=v.version_id'
                conditions.append('learning_fts MATCH ?')
                parameters.append(' OR '.join('"' + term + '"' for term in terms))
                order = 'bm25(learning_fts),v.version_id'
            if exact_scope_paths is not None:
                # JSON comparison is ordered by the prepared canonical scope.
                conditions.append("json_extract(v.scope_json,'$.permitted_paths')=?")
                parameters.append(json.dumps(sorted(exact_scope_paths), separators=(',', ':')))
            parameters.append(request.limit + 1)
            source = 'learning_versions v' if request.include_history else 'learning_current c JOIN learning_versions v ON v.version_id=c.version_id'
            rows = connection.execute('SELECT v.* FROM ' + source + join +
                ' WHERE ' + ' AND '.join(conditions) + ' ORDER BY ' + order + ' LIMIT ?', parameters).fetchall()
            lessons, total_bytes = [], 0
            truncated = len(rows) > request.limit
            for row in rows[:request.limit]:
                content = json.loads(self.store.read_object(row['content_digest']))
                scope = content.get('scope', {})
                if (content.get('project_id') != self.store.project_id or content.get('source_job_id') != row['source_job_id']
                        or content_digest(scope) != row['lesson_key'] or json_text(scope) != row['scope_json']
                        or scope.get('profile') != row['profile'] or scope.get('action') != row['action']):
                    raise LaneError('LEARNING_INTEGRITY_FAILED', 'The learned observation differs from its source identity.')
                with self.project.lane('receipts').connection(read_only=True) as receipts:
                    receipt = receipts.execute("SELECT body_json FROM receipts WHERE receipt_id=? AND kind='delta_exit_verified'",
                                                 (row['source_receipt_id'],)).fetchone()
                evidence = json.loads(receipt[0]) if receipt else {}
                with self.project.lane('plan').connection(read_only=True) as plan:
                    exit_row = plan.execute('SELECT receipt_id,verification_object FROM delta_exits WHERE job_id=?',
                                            (row['source_job_id'],)).fetchone()
                if (evidence.get('status') != 'passed' or evidence.get('job_id') != row['source_job_id']
                        or evidence.get('project_id') != self.store.project_id
                        or evidence.get('verification_object') != content.get('verification_object')
                        or exit_row is None or exit_row['receipt_id'] != row['source_receipt_id']
                        or exit_row['verification_object'] != content.get('verification_object')):
                    raise LaneError('LEARNING_INTEGRITY_FAILED', 'The verified-exit source for this observation is inconsistent.')
                item = {'version_id': row['version_id'], 'version': row['version'], 'lesson_key': row['lesson_key'],
                        'state': row['state'],
                        'content_digest': row['content_digest'], 'source_receipt_id': row['source_receipt_id'], 'observation': content}
                size = len(json_text(item).encode())
                if total_bytes + size > 131_072:
                    truncated = True
                    break
                total_bytes += size
                lessons.append(item)
        return LearningPage(project_id=self.store.project_id, lessons=lessons, truncated=truncated)

    def verify_history(self, *, limit=50_000):
        if not 1 <= limit <= 50_000:
            raise LaneError('LEARNING_HISTORY_BUDGET', 'Select a bounded history verification limit.')
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            rows = connection.execute('SELECT * FROM learning_events ORDER BY sequence LIMIT ?', (limit + 1,)).fetchall()
            if len(rows) > limit:
                raise LaneError('LEARNING_HISTORY_BUDGET', 'This history exceeds the selected verification budget.')
            previous = None
            for sequence, row in enumerate(rows, 1):
                value = dict(row)
                digest = value.pop('digest')
                if row['sequence'] != sequence or row['previous_digest'] != previous or content_digest(value) != digest:
                    raise LaneError('LEARNING_HISTORY_INTEGRITY', 'The Learning event chain is inconsistent.')
                previous = digest
            return {'verified_events': len(rows), 'head': previous}

    def revoke(self, request, lease, *, actor_id):
        self.initialize(lease)
        reason = redact(request.reason)
        with lease.transaction('learning') as connection:
            row = connection.execute('SELECT * FROM learning_versions WHERE version_id=?', (request.version_id,)).fetchone()
            if row is None or row['content_digest'] != request.expected_digest:
                raise LaneError('LEARNING_VERSION_MISMATCH', 'Select the exact current learned observation and digest.')
            prior = connection.execute('SELECT * FROM learning_events WHERE event_id=?', (request.request_id,)).fetchone()
            body = {'reason': reason, 'content_digest': request.expected_digest}
            if prior:
                if prior['version_id'] != request.version_id or prior['kind'] != 'revoked' or prior['actor_id'] != actor_id or json.loads(prior['body_json']) != body:
                    raise LaneError('LEARNING_REQUEST_CONFLICT', 'This request ID belongs to another Learning decision.')
                return LearningRevoked(version_id=row['version_id'], lesson_key=row['lesson_key'])
            current = connection.execute('SELECT version_id FROM learning_current WHERE lesson_key=?', (row['lesson_key'],)).fetchone()
            if row['state'] != 'active' or not current or current[0] != row['version_id']:
                raise LaneError('LEARNING_VERSION_NOT_CURRENT', 'Only the current active version can be revoked.')
            connection.execute("UPDATE learning_versions SET state='revoked' WHERE version_id=?", (row['version_id'],))
            connection.execute('INSERT INTO learning_controls VALUES(?,?,?,?)', (row['lesson_key'], actor_id, reason, now()))
            self._event(connection, row['version_id'], 'revoked', actor_id, body, event_id=request.request_id)
        return LearningRevoked(version_id=row['version_id'], lesson_key=row['lesson_key'])


def learning_view(store, scope):
    from .lane_contract import ViewGraph
    page = LearningStore(store).read(LearningRead(limit=min(scope.node_limit, 20), query=scope.query,
                                                include_history=scope.include_history))
    graph = ViewGraph(store.project_id, scope)
    versions = {}
    for item in page.lessons:
        lesson = graph.node('learning_version', item['version_id'], item['observation']['summary'],
            state=item['state'], locator={'content_digest': item['content_digest'], 'version': item['version']})
        receipt = graph.node('verified_exit', item['source_receipt_id'], 'Verified Delta exit',
            locator={'receipt_id': item['source_receipt_id'], 'job_id': item['observation']['source_job_id']})
        graph.edge(receipt, lesson, 'VERIFIED_SOURCE_OF')
        versions[(item['lesson_key'], item['version'])] = lesson
    if scope.include_history:
        for (key, version), node in versions.items():
            if version > 1:
                graph.edge(versions.get((key, version - 1)), node, 'PREVIOUS_VERSION')
    graph.truncated |= page.truncated
    return graph.result()


def register_learning_actions(engine):
    def import_provenance(context, request):
        project = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(project, kind='capture') as lease:
            return record_host_memory_import(project, request, lease, actor_id=context.client_id)

    engine.registry.register(ActionSpec('learning_record_host_memory_import',
        'Record explicit host-memory provenance and its exact Plan-task link without reading host memory or creating Learning.',
        HostMemoryImport, HostMemoryImported, import_provenance,
        permission='write', profile='learning', mutates=True, workflow='manage-project-lessons'))
    engine.registry.register(ActionSpec('learning_read', 'Retrieve a bounded project-local slice of verified execution observations.',
        LearningRead, LearningPage, lambda context, request: LearningStore(engine.directory.open(context.project_id)).read(request),
        profile='learning', queryable_in_delta=True, cross_project_read=True, read_migrations=LEARNING_MIGRATIONS, workflow='manage-project-lessons',
        search=SearchRoute(('learning',), 'lessons')))

    def revoke(context, request):
        store = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(store) as lease:
            return LearningStore(store).revoke(request, lease, actor_id=context.client_id)

    engine.registry.register(ActionSpec('learning_revoke', 'Revoke the exact current Learning observation and suppress automatic reactivation.',
        LearningRevoke, LearningRevoked, revoke, permission='write', profile='learning', mutates=True, workflow='manage-project-lessons'))
