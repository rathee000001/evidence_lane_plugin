"""Durable project session records owned by the Receipts lane.

Session events bind runtime policy and client ownership. They do not create a
ninth project authority or carry Plan, source, memory or conversation payloads.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .errors import LaneError
from .migrations import Migration, apply_migrations
from .plan_runtime import content_digest
from .registry import Contract
from .sdk import UUID_PATTERN
from .storage import now

DIGEST = r'^[0-9a-f]{64}$'
REPORTED_SESSION = r'^[A-Za-z0-9._-]+$'


class SessionStatusRequest(Contract):
    pass


class SessionBoot(Contract):
    reported_session_id: str = Field(min_length=1, max_length=128, pattern=REPORTED_SESSION)
    expected_root_pv_digest: str = Field(pattern=DIGEST)
    require_native_attestation: bool = False


class SessionResume(SessionBoot):
    session_id: str = Field(pattern=UUID_PATTERN)
    expected_generation: int = Field(ge=1)
    expected_event_digest: str = Field(pattern=DIGEST)
    continuation_id: str | None = Field(default=None, pattern=UUID_PATTERN)


class SessionExit(Contract):
    session_id: str = Field(pattern=UUID_PATTERN)
    expected_generation: int = Field(ge=1)
    expected_event_digest: str = Field(pattern=DIGEST)
    reason: str = Field(min_length=1, max_length=1000)


class SessionBoundaryRead(Contract):
    kind: Literal['ordinary_turn', 'delta_append', 'session_exit', 'state_travel', 'goal_completion']
    source_event_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    source_cursor: str | None = Field(default=None, pattern=DIGEST)
    continuation_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    continuation_digest: str | None = Field(default=None, pattern=DIGEST)

    @model_validator(mode='after')
    def paired_references(self):
        if (self.source_event_id is None) != (self.source_cursor is None):
            raise ValueError('Supply the exact source event and cursor together')
        if (self.continuation_id is None) != (self.continuation_digest is None):
            raise ValueError('Supply the exact continuation and digest together')
        if self.continuation_id is not None and self.kind != 'state_travel':
            raise ValueError('A continuation reference belongs to State Travel')
        return self


class SessionBoundaryResult(Contract):
    project_id: str
    kind: str
    policy_event: str
    flash_digest: str
    policy: dict
    source_reference: dict | None = None
    continuation_reference: dict | None = None
    evidence_state: str
    next_requirement: str
    terminal_exit_allowed: Literal[False] = False
    terminal_receipt_emitted: Literal[False] = False
    native_task_attestation: Literal['not_provided'] = 'not_provided'
    plan_changed: Literal[False] = False
    project_mutated: Literal[False] = False


class SessionResult(Contract):
    project_id: str
    state: Literal['none', 'active', 'closed']
    session_id: str | None = None
    generation: int = 0
    owner_client_id: str | None = None
    owner_engine_id: str | None = None
    reported_session_id: str | None = None
    flash_digest: str | None = None
    event_digest: str | None = None
    runtime_package_digest: str | None = None
    root_pv: dict[str, JsonValue]
    plan: dict[str, JsonValue]
    capture_bound: bool = False
    owner_authenticated: bool = False
    flash_current: bool = False
    context: str | None = None
    host_observation: dict[str, JsonValue] | None = None
    operation: str = 'status'
    observation_scope: Literal['current_read', 'at_transition_commit'] = 'current_read'
    exit_reason: str | None = None
    storage_route: dict[str, JsonValue] | None = None
    state_authority: Literal['receipts_lane_sqlite'] = 'receipts_lane_sqlite'
    identity_scope: Literal['authenticated_engine_client'] = 'authenticated_engine_client'
    reported_session_identity: Literal['client_report_only'] = 'client_report_only'
    native_task_attestation: Literal['not_provided'] = 'not_provided'
    hook_execution_attested: Literal[False] = False
    native_plan_updated: Literal[False] = False
    jobs_replayed: Literal[False] = False
    terminal_exit: Literal[False] = False
    native_goal_completed: Literal[False] = False


SESSION_MIGRATIONS = (Migration('sessions', 1, 'Project session head and immutable attributed transitions', (
    '''CREATE TABLE sessions_records (session_id TEXT PRIMARY KEY,
       state TEXT NOT NULL CHECK(state IN ('active','closed')), generation INTEGER NOT NULL CHECK(generation>=1),
       owner_client_id TEXT NOT NULL, owner_engine_id TEXT NOT NULL, reported_session_id TEXT NOT NULL,
       flash_digest TEXT NOT NULL, runtime_package_digest TEXT NOT NULL, event_digest TEXT NOT NULL,
       created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''',
    "CREATE UNIQUE INDEX sessions_one_active ON sessions_records(state) WHERE state='active'",
    '''CREATE TABLE sessions_current (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
       session_id TEXT NOT NULL REFERENCES sessions_records(session_id))''',
    '''CREATE TABLE sessions_events (sequence INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
       session_id TEXT NOT NULL REFERENCES sessions_records(session_id) DEFERRABLE INITIALLY DEFERRED,
       generation INTEGER NOT NULL, action TEXT NOT NULL, client_id TEXT NOT NULL, input_digest TEXT NOT NULL,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)), previous_digest TEXT, digest TEXT NOT NULL UNIQUE,
       created_at TEXT NOT NULL, UNIQUE(session_id,generation))''',
)),)


class SessionAuthority:
    def __init__(self, project):
        self.project = project
        self.store = project.lane('receipts')

    def initialize(self, lease):
        apply_migrations(self.store, SESSION_MIGRATIONS, writer=lease)

    def verify_history(self, *, limit=10000):
        """Verify immutable transitions and every session head without resuming."""
        if not 1 <= limit <= 50000:
            raise LaneError('SESSION_HISTORY_BUDGET', 'Select a bounded session history verifier.')
        with self.store.connection(read_only=True) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='sessions_events'").fetchone():
                return {'events_verified': 0, 'sessions_verified': 0}
            rows = connection.execute('SELECT * FROM sessions_events ORDER BY sequence LIMIT ?', (limit+1,)).fetchall()
            if len(rows) > limit:
                raise LaneError('SESSION_HISTORY_BUDGET', 'Session history exceeds the selected verification budget.')
            previous = None
            heads = {}
            for index, row in enumerate(rows, 1):
                event = dict(row)
                digest = event.pop('digest')
                if len(event['body_json'].encode()) > 262144:
                    raise LaneError('SESSION_HISTORY_BUDGET', 'A session event exceeds its bounded record size.')
                body = json.loads(event['body_json'])
                prior = heads.get(event['session_id'])
                if (event['sequence'] != index or event['previous_digest'] != previous
                        or content_digest(event) != digest or body.get('session_id') != event['session_id']
                        or body.get('project_id') != self.project.project_id or body.get('generation') != event['generation']
                        or event['generation'] != (prior['generation']+1 if prior else 1)):
                    raise LaneError('SESSION_HISTORY_INTEGRITY', 'Session history differs from its exact transition chain.')
                heads[event['session_id']] = {**body, 'event_digest': digest}
                previous = digest
            records = connection.execute('SELECT * FROM sessions_records ORDER BY session_id LIMIT ?', (limit+1,)).fetchall()
            if len(records) > limit or {row['session_id'] for row in records} != set(heads):
                raise LaneError('SESSION_HISTORY_INTEGRITY', 'Session records differ from their transition histories.')
            fields = ('state', 'generation', 'owner_client_id', 'owner_engine_id', 'reported_session_id',
                      'flash_digest', 'runtime_package_digest', 'event_digest')
            for row in records:
                if any(row[field] != heads[row['session_id']].get(field) for field in fields):
                    raise LaneError('SESSION_HISTORY_INTEGRITY', 'A session record differs from its last transition.')
            current = self.current(connection)
            if rows and (current is None or current['session_id'] != rows[-1]['session_id']):
                raise LaneError('SESSION_HISTORY_INTEGRITY', 'The session selector differs from the last transition.')
            return {'events_verified': len(rows), 'sessions_verified': len(records)}

    @staticmethod
    def current(connection):
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='sessions_current'").fetchone():
            return None
        row = connection.execute('SELECT r.* FROM sessions_current c JOIN sessions_records r USING(session_id) WHERE c.singleton=1').fetchone()
        if row is None:
            return None
        event = connection.execute('SELECT * FROM sessions_events WHERE digest=?', (row['event_digest'],)).fetchone()
        if event is None:
            raise LaneError('SESSION_HISTORY_INTEGRITY', 'The session head has no matching event.')
        checked = dict(event)
        digest = checked.pop('digest')
        if content_digest(checked) != digest:
            raise LaneError('SESSION_HISTORY_INTEGRITY', 'The current session event changed.')
        body = json.loads(event['body_json'])
        fields = ('session_id', 'generation', 'state', 'owner_client_id', 'owner_engine_id',
                  'reported_session_id', 'flash_digest', 'runtime_package_digest')
        if any(body[key] != row[key] for key in fields):
            raise LaneError('SESSION_HISTORY_INTEGRITY', 'The session head differs from its immutable transition.')
        return dict(row)

    @staticmethod
    def replay(connection, request_id, action, client_id, input_digest):
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='sessions_events'").fetchone():
            return None
        row = connection.execute('SELECT * FROM sessions_events WHERE request_id=?', (request_id,)).fetchone()
        if row is None:
            return None
        if (row['action'], row['client_id'], row['input_digest']) != (action, client_id, input_digest):
            raise LaneError('SESSION_REQUEST_CONFLICT', 'The request identifier belongs to another session transition.')
        checked = dict(row)
        digest = checked.pop('digest')
        if content_digest(checked) != digest:
            raise LaneError('SESSION_HISTORY_INTEGRITY', 'The saved transition changed.')
        return {**json.loads(row['body_json']), 'event_digest': digest}

    def append(self, connection, *, action, client_id, request_id, input_digest, body):
        from .state_law import transition
        current = self.current(connection)
        transition(current['state'] if current else None, action, body['state'], domain='project_session')
        prior = connection.execute('SELECT sequence,digest FROM sessions_events ORDER BY sequence DESC LIMIT 1').fetchone()
        stamp = now()
        event = {'sequence': prior['sequence'] + 1 if prior else 1, 'request_id': request_id,
            'session_id': body['session_id'], 'generation': body['generation'], 'action': action,
            'client_id': client_id, 'input_digest': input_digest, 'body_json': json.dumps(body, sort_keys=True, separators=(',', ':')),
            'previous_digest': prior['digest'] if prior else None, 'created_at': stamp}
        digest = content_digest(event)
        connection.execute('INSERT INTO sessions_events VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (event['sequence'], request_id, body['session_id'], body['generation'], action, client_id,
             input_digest, event['body_json'], event['previous_digest'], digest, stamp))
        values = [body[key] for key in ('session_id', 'state', 'generation', 'owner_client_id', 'owner_engine_id',
                  'reported_session_id', 'flash_digest', 'runtime_package_digest')]
        connection.execute('''INSERT INTO sessions_records VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET state=excluded.state,generation=excluded.generation,
            owner_client_id=excluded.owner_client_id,owner_engine_id=excluded.owner_engine_id,
            reported_session_id=excluded.reported_session_id,flash_digest=excluded.flash_digest,
            runtime_package_digest=excluded.runtime_package_digest,event_digest=excluded.event_digest,updated_at=excluded.updated_at''',
            (*values, digest, stamp, stamp))
        connection.execute('INSERT INTO sessions_current VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET session_id=excluded.session_id',
                           (body['session_id'],))
        self.store.append_receipt(action, {'session_id': body['session_id'], 'generation': body['generation'],
            'event_digest': digest, 'client_id': client_id, 'request_id': request_id,
            'identity_scope': 'authenticated_engine_client'}, connection=connection)
        return {**body, 'event_digest': digest}
