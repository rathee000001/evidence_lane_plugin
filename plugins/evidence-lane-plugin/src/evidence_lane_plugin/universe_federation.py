"""Hash-only federation in a separately selected coordinator project's Universe.

The coordinator is never a member. Member projects retain their own databases,
Universe and grants. This reuses the existing writer/publication/recovery owner
instead of reviving Connector Brain or trusting an arbitrary caller database.
"""
from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import Field, JsonValue, field_validator

from .errors import LaneError
from .lane_reader import LaneReader
from .migrations import Migration, apply_migrations, read_compatibility
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import bounded_project_read, json_text, now
from .universe_snapshot import UniverseInspect, digest, inspect_project

DIGEST = r'^[0-9a-f]{64}$'
MINI_ID = r'^mini_[0-9a-f]{64}$'
RELATION = r'^[a-z][a-z0-9_]{0,63}$'


class FederationCreate(Contract):
    name: str = Field(min_length=1, max_length=160)


class FederationRegister(Contract):
    target_project_id: str = Field(pattern=UUID_PATTERN)
    expected_version: int = Field(default=0, ge=0)
    expected_snapshot_sha256: str = Field(pattern=DIGEST)
    inspection: UniverseInspect = Field(default_factory=UniverseInspect)

    @field_validator('inspection')
    @classmethod
    def verified_files(cls, value):
        if not value.verify_files:
            raise ValueError('Federation registration requires verified registered-file identities')
        return value


class FederationGrant(Contract):
    source_mini_brain_id: str = Field(pattern=MINI_ID)
    target_mini_brain_id: str = Field(pattern=MINI_ID)
    relation: str = Field(pattern=RELATION)
    lifetime_seconds: int = Field(default=3600, ge=1, le=86400)


class FederationRevoke(Contract):
    grant_id: str = Field(pattern=UUID_PATTERN)


class FederationLink(Contract):
    grant_id: str = Field(pattern=UUID_PATTERN)
    evidence: dict[str, str] = Field(min_length=1, max_length=16)

    @field_validator('evidence')
    @classmethod
    def hashes_only(cls, value):
        if any(not re.fullmatch(r'[a-z][a-z0-9_]{0,63}_sha256', key)
               or not re.fullmatch(DIGEST, item) for key, item in value.items()):
            raise ValueError('Federation evidence requires bounded named SHA-256 references only')
        return value


class FederationRead(Contract):
    view: Literal['members', 'mini_brains', 'links', 'grants'] = 'members'
    after_id: str = Field(default='', max_length=80, pattern=r'^[a-zA-Z0-9_-]*$')
    limit: int = Field(default=20, ge=1, le=100)
    target_project_id: str | None = Field(default=None, pattern=UUID_PATTERN)


class FederationVerify(Contract):
    max_records: int = Field(default=1000, ge=1, le=10000)


class FederationResult(Contract):
    project_id: str
    federation_id: str
    state: str
    result: dict[str, JsonValue]
    writes_performed: bool
    member_projects_mutated: Literal[False] = False
    raw_cross_project_payload_copied: Literal[False] = False
    project_truth_merged: Literal[False] = False


FEDERATION_MIGRATIONS = (Migration('federation', 1, 'Separate coordinator hash federation, historical heads and explicit expiring edge grants', (
    """CREATE TABLE federation_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        federation_id TEXT NOT NULL UNIQUE, body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL)""",
    """CREATE TABLE federation_members (project_id TEXT PRIMARY KEY, version INTEGER NOT NULL,
        body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL)""",
    """CREATE TABLE federation_member_history (project_id TEXT NOT NULL, version INTEGER NOT NULL,
        body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL, PRIMARY KEY(project_id,version))""",
    """CREATE TABLE federation_mini_brains (mini_brain_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
        lane_id TEXT NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL)""",
    'CREATE INDEX federation_mini_project ON federation_mini_brains(project_id,lane_id,mini_brain_id)',
    """CREATE TABLE federation_lane_heads (project_id TEXT NOT NULL, lane_id TEXT NOT NULL,
        mini_brain_id TEXT NOT NULL REFERENCES federation_mini_brains(mini_brain_id), PRIMARY KEY(project_id,lane_id))""",
    """CREATE TABLE federation_grants (grant_id TEXT PRIMARY KEY,
        body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL)""",
    """CREATE TABLE federation_revocations (grant_id TEXT PRIMARY KEY REFERENCES federation_grants(grant_id),
        body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL)""",
    """CREATE TABLE federation_links (edge_id TEXT PRIMARY KEY, grant_id TEXT NOT NULL REFERENCES federation_grants(grant_id),
        body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL)""",
)),)


def checked(row):
    if row is None:
        raise LaneError('FEDERATION_RECORD_REQUIRED', 'Select an existing federation reference.')
    try:
        if len(row['body_json'].encode()) > 1048576:
            raise ValueError()
        body = json.loads(row['body_json'])
        if not isinstance(body, dict) or digest(body) != row['digest']:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise LaneError('FEDERATION_INTEGRITY', 'A federation record differs from its recorded hash.') from None
    return body


def validate_grant(body, grant_id, federation_id):
    try:
        expires = datetime.fromisoformat(body['expires_at'])
        issued = datetime.fromisoformat(body['granted_at'])
        if (body['grant_id'] != grant_id or body['federation_id'] != federation_id
                or expires.tzinfo is None or issued.tzinfo is None
                or not 0 < (expires - issued).total_seconds() <= 86401):
            raise ValueError()
        FederationGrant(source_mini_brain_id=body['source_mini_brain_id'],
            target_mini_brain_id=body['target_mini_brain_id'], relation=body['relation'])
    except (ValueError, KeyError, TypeError):
        raise LaneError('FEDERATION_INTEGRITY', 'The grant has invalid federation, endpoint or lifetime evidence.') from None
    return expires


class UniverseFederation:
    def __init__(self, project):
        self.project = project
        self.store = project.lane('universe')
        self._federation_id = None

    def identity(self):
        read_compatibility(self.store, FEDERATION_MIGRATIONS)
        with self.store.connection(read_only=True) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='federation_identity'").fetchone():
                raise LaneError('FEDERATION_NOT_SELECTED', 'Create a federation in this separately selected coordinator project first.')
            row = connection.execute('SELECT * FROM federation_identity').fetchone()
            if row is None:
                raise LaneError('FEDERATION_NOT_SELECTED', 'Create the selected federation first.')
            body = checked(row)
            if body['coordinator_project_id'] != self.project.project_id or body['federation_id'] != row['federation_id']:
                raise LaneError('FEDERATION_INTEGRITY', 'The federation belongs to another coordinator project.')
            self._federation_id = body['federation_id']
            return body

    def result(self, state, body, *, changed=False):
        result = FederationResult(project_id=self.project.project_id, federation_id=self._federation_id or self.identity()['federation_id'],
            state=state, result=body, writes_performed=changed)
        if len(result.model_dump_json().encode()) > 262144:
            raise LaneError('FEDERATION_OUTPUT_BUDGET', 'Read a smaller page of federation references.')
        return result

    def create(self, request, lease, actor):
        apply_migrations(self.store, FEDERATION_MIGRATIONS, writer=lease)
        with lease.transaction('universe') as connection:
            prior = connection.execute('SELECT * FROM federation_identity').fetchone()
            if prior:
                body = checked(prior)
                if body['name'] != request.name:
                    raise LaneError('FEDERATION_EXISTS', 'The selected coordinator already owns a different named federation.')
                self._federation_id = body['federation_id']
                return self.result('reused', body)
            body = {'federation_id': str(uuid4()), 'coordinator_project_id': self.project.project_id,
                    'name': request.name, 'created_at': now(), 'created_by': actor}
            self._federation_id = body['federation_id']
            connection.execute('INSERT INTO federation_identity VALUES(1,?,?,?)',
                (body['federation_id'], json_text(body), digest(body)))
            self.store.append_receipt('federation_created', body, connection=connection)
            return self.result('created', body, changed=True)

    def member(self, project_id):
        self.identity()
        with self.store.connection(read_only=True) as connection:
            row = connection.execute('SELECT * FROM federation_members WHERE project_id=?', (project_id,)).fetchone()
            if row is None:
                return None
            body = checked(row)
            if body['project_id'] != row['project_id'] or body['version'] != row['version']:
                raise LaneError('FEDERATION_INTEGRITY', 'A member binding differs from its indexed identity.')
            return body

    def register(self, request, snapshot, lease, actor):
        self.identity()
        if snapshot.project_id == self.project.project_id or snapshot.project_id != request.target_project_id:
            raise LaneError('FEDERATION_MEMBER_SCOPE', 'Select a member distinct from the federation coordinator.')
        if snapshot.snapshot_sha256 != request.expected_snapshot_sha256:
            raise LaneError('FEDERATION_SNAPSHOT_CHANGED', 'Inspect the member again and select its exact current snapshot.')
        prior = self.member(snapshot.project_id)
        if prior and prior['snapshot_sha256'] == snapshot.snapshot_sha256:
            return self.result('reused', {'member': prior, 'changed_lane_ids': [], 'removed_lane_ids': []})
        if (prior['version'] if prior else 0) != request.expected_version:
            raise LaneError('FEDERATION_VERSION_CONFLICT', 'Select the member current registration version.')
        with lease.transaction('universe') as connection:
            old = {row['lane_id']: row['mini_brain_id'] for row in connection.execute(
                'SELECT * FROM federation_lane_heads WHERE project_id=?', (snapshot.project_id,))}
            desired = {}
            for lane in snapshot.lanes:
                body = {key: value for key, value in lane.items() if key != 'mini_brain_id'}
                key = lane['mini_brain_id']
                if key != 'mini_' + digest(body):
                    raise LaneError('FEDERATION_INTEGRITY', 'A lane reference differs from its content identity.')
                existing = connection.execute('SELECT * FROM federation_mini_brains WHERE mini_brain_id=?', (key,)).fetchone()
                if existing and checked(existing) != body:
                    raise LaneError('FEDERATION_INTEGRITY', 'The historical mini-brain reference changed.')
                if not existing:
                    connection.execute('INSERT INTO federation_mini_brains VALUES(?,?,?,?,?)',
                        (key, snapshot.project_id, lane['lane_id'], json_text(body), digest(body)))
                desired[lane['lane_id']] = key
                if old.get(lane['lane_id']) != key:
                    connection.execute('INSERT OR REPLACE INTO federation_lane_heads VALUES(?,?,?)',
                        (snapshot.project_id, lane['lane_id'], key))
            removed = sorted(set(old) - set(desired))
            for lane_id in removed:
                connection.execute('DELETE FROM federation_lane_heads WHERE project_id=? AND lane_id=?',
                    (snapshot.project_id, lane_id))
            body = {'project_id': snapshot.project_id, 'version': request.expected_version + 1,
                'project_root_identity_sha256': snapshot.project_root_identity_sha256,
                'root_pv': snapshot.root_pv, 'snapshot_sha256': snapshot.snapshot_sha256,
                'lane_heads': desired, 'registered_at': now(), 'registered_by': actor,
                'observation_scope': 'captured_coherent_member_snapshot'}
            values = (snapshot.project_id, body['version'], json_text(body), digest(body))
            connection.execute('INSERT INTO federation_member_history VALUES(?,?,?,?)', values)
            connection.execute('INSERT OR REPLACE INTO federation_members VALUES(?,?,?,?)', values)
            result = {'member': body, 'changed_lane_ids': sorted(key for key in desired if old.get(key) != desired[key]),
                      'removed_lane_ids': removed, 'historical_mini_brain_refs_preserved': True}
            self.store.append_receipt('federation_member_registered', result, connection=connection)
            return self.result('advanced', result, changed=True)

    def endpoints(self, source, target):
        self.identity()
        with self.store.connection(read_only=True) as connection:
            rows = [connection.execute('SELECT * FROM federation_mini_brains WHERE mini_brain_id=?', (key,)).fetchone()
                    for key in (source, target)]
            bodies = [checked(row) for row in rows]
            for key, row, body in zip((source, target), rows, bodies):
                if key != 'mini_' + digest(body) or any(row[name] != body[name] for name in ('project_id', 'lane_id')):
                    raise LaneError('FEDERATION_INTEGRITY', 'A mini-brain index differs from its stored reference.')
            if bodies[0]['project_id'] == bodies[1]['project_id']:
                raise LaneError('FEDERATION_EDGE_SCOPE', 'Cross-project edges require two distinct member projects.')
            return bodies

    def grant(self, request, lease, actor):
        self.endpoints(request.source_mini_brain_id, request.target_mini_brain_id)
        body = {'grant_id': str(uuid4()), 'federation_id': self.identity()['federation_id'],
            'source_mini_brain_id': request.source_mini_brain_id, 'target_mini_brain_id': request.target_mini_brain_id,
            'relation': request.relation, 'granted_by': actor, 'granted_at': now(),
            'expires_at': (datetime.now(UTC) + timedelta(seconds=request.lifetime_seconds)).isoformat()}
        with lease.transaction('universe') as connection:
            connection.execute('INSERT INTO federation_grants VALUES(?,?,?)', (body['grant_id'], json_text(body), digest(body)))
            self.store.append_receipt('federation_edge_granted', body, connection=connection)
        return self.result('granted', {**body, 'explicit_grant_sha256': digest(body)}, changed=True)

    def grant_record(self, grant_id, *, require_active=True):
        identity = self.identity()
        with self.store.connection(read_only=True) as connection:
            body = checked(connection.execute('SELECT * FROM federation_grants WHERE grant_id=?', (grant_id,)).fetchone())
            expires = validate_grant(body, grant_id, identity['federation_id'])
            revoked = connection.execute('SELECT * FROM federation_revocations WHERE grant_id=?', (grant_id,)).fetchone()
            if revoked:
                checked(revoked)
            if require_active and (revoked or expires <= datetime.now(UTC)):
                raise LaneError('FEDERATION_GRANT_INACTIVE', 'The explicit edge grant expired or was revoked.')
            return body

    def revoke(self, request, lease, actor):
        self.grant_record(request.grant_id, require_active=False)
        with lease.transaction('universe') as connection:
            prior = connection.execute('SELECT * FROM federation_revocations WHERE grant_id=?', (request.grant_id,)).fetchone()
            if prior:
                return self.result('reused', checked(prior))
            body = {'grant_id': request.grant_id, 'revoked_by': actor, 'revoked_at': now()}
            connection.execute('INSERT INTO federation_revocations VALUES(?,?,?)', (request.grant_id, json_text(body), digest(body)))
            self.store.append_receipt('federation_edge_grant_revoked', body, connection=connection)
        return self.result('revoked', body, changed=True)

    def revoke_recovered_grants(self, connection, backup_digest):
        """Invalidate restored mutation grants in the caller's recovery commit.

        Grant records and existing links stay immutable. The supplied connection
        is the recovered project's Universe lane, inside Root PV coordination.
        """
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='federation_identity'").fetchone():
            return 0
        if not connection.execute('SELECT 1 FROM federation_identity').fetchone():
            return 0
        identity = self.identity()
        rows = connection.execute('SELECT g.* FROM federation_grants g LEFT JOIN federation_revocations r '
            'USING(grant_id) WHERE r.grant_id IS NULL ORDER BY g.grant_id LIMIT 10001').fetchall()
        if len(rows) > 10000:
            raise LaneError('RECOVERY_FEDERATION_BUDGET', 'The recovered federation exceeds its bounded grant inventory.')
        for row in rows:
            grant = checked(row)
            validate_grant(grant, row['grant_id'], identity['federation_id'])
            body = {'grant_id': row['grant_id'], 'revoked_by': 'administrative-recovery',
                    'revoked_at': now(), 'backup_digest': backup_digest}
            connection.execute('INSERT INTO federation_revocations VALUES(?,?,?)',
                (row['grant_id'], json_text(body), digest(body)))
            self.store.append_receipt('federation_edge_grant_revoked', body, connection=connection)
        return len(rows)

    def existing_link(self, request):
        grant = self.grant_record(request.grant_id)
        core = {'source_mini_brain_id': grant['source_mini_brain_id'], 'target_mini_brain_id': grant['target_mini_brain_id'],
                'relation': grant['relation'], 'explicit_grant_sha256': digest(grant), 'evidence': request.evidence}
        key = 'uedge_' + digest(core)
        with self.store.connection(read_only=True) as connection:
            row = connection.execute('SELECT * FROM federation_links WHERE edge_id=?', (key,)).fetchone()
            if row is None:
                return None
            body = checked(row)
            if body['edge_id'] != key or body['grant_id'] != request.grant_id or any(body.get(name) != value for name, value in core.items()):
                raise LaneError('FEDERATION_INTEGRITY', 'The historical edge differs from its exact granted content.')
            return body

    def link(self, request, lease):
        grant = self.grant_record(request.grant_id)
        self.endpoints(grant['source_mini_brain_id'], grant['target_mini_brain_id'])
        core = {'source_mini_brain_id': grant['source_mini_brain_id'], 'target_mini_brain_id': grant['target_mini_brain_id'],
                'relation': grant['relation'], 'explicit_grant_sha256': digest(grant), 'evidence': request.evidence}
        edge_id = 'uedge_' + digest(core)
        with lease.transaction('universe') as connection:
            prior = connection.execute('SELECT * FROM federation_links WHERE edge_id=?', (edge_id,)).fetchone()
            if prior:
                return self.result('reused', checked(prior))
            body = {**core, 'edge_id': edge_id, 'grant_id': request.grant_id, 'recorded_at': now()}
            connection.execute('INSERT INTO federation_links VALUES(?,?,?,?)',
                (edge_id, request.grant_id, json_text(body), digest(body)))
            self.store.append_receipt('federation_edge_linked', body, connection=connection)
        return self.result('appended', body, changed=True)

    def read(self, request):
        self.identity()
        table, key = {'members': ('federation_members', 'project_id'), 'mini_brains': ('federation_mini_brains', 'mini_brain_id'),
            'links': ('federation_links', 'edge_id'), 'grants': ('federation_grants', 'grant_id')}[request.view]
        if request.target_project_id and request.view not in {'members', 'mini_brains'}:
            raise LaneError('FEDERATION_QUERY_SCOPE', 'Project filters apply to member and mini-brain references.')
        with bounded_project_read(self.project.root, time.monotonic() + 10), self.store.connection(read_only=True) as connection:
            clauses, parameters = [key + '>?'], [request.after_id]
            if request.target_project_id:
                clauses.append('project_id=?')
                parameters.append(request.target_project_id)
            rows = connection.execute('SELECT * FROM ' + table + ' WHERE ' + ' AND '.join(clauses)
                + ' ORDER BY ' + key + ' LIMIT ?', (*parameters, request.limit + 1)).fetchall()
            records = []
            for row in rows[:request.limit]:
                body = checked(row)
                if request.view == 'mini_brains':
                    if row[key] != 'mini_' + digest(body):
                        raise LaneError('FEDERATION_INTEGRITY', 'A mini-brain index differs from its content identity.')
                    body = {**body, key: row[key]}
                elif body.get(key) != row[key]:
                    raise LaneError('FEDERATION_INTEGRITY', 'A federation index differs from its recorded identity.')
                records.append(body)
        return self.result('read', {'records': records, 'truncated': len(rows) > request.limit,
            'last_id': rows[min(len(rows), request.limit) - 1][key] if rows else request.after_id,
            'member_live_access_checked': False, 'reference_scope': 'recorded_hashes'})

    def verify(self, request):
        self.identity()
        records, remaining = {}, request.max_records
        deadline = time.monotonic() + 10
        with bounded_project_read(self.project.root, deadline), self.store.connection(read_only=True) as connection:
            for table in ('federation_identity', 'federation_members', 'federation_member_history', 'federation_mini_brains',
                          'federation_grants', 'federation_revocations', 'federation_links', 'federation_lane_heads'):
                if time.monotonic() >= deadline:
                    raise LaneError('FEDERATION_VERIFY_TIMEOUT', 'Use a smaller bounded federation verification scope.')
                rows = connection.execute('SELECT * FROM ' + table + ' LIMIT ?', (remaining + 1,)).fetchall()
                remaining -= len(rows)
                if remaining < 0:
                    raise LaneError('FEDERATION_VERIFY_BUDGET', 'Increase the bounded integrity budget or inspect reference pages.')
                records[table] = rows
                if table != 'federation_lane_heads':
                    for row in rows:
                        checked(row)
            minis = {row['mini_brain_id']: checked(row) for row in records['federation_mini_brains']}
            for row in records['federation_mini_brains']:
                body = minis[row['mini_brain_id']]
                if (row['mini_brain_id'] != 'mini_' + digest(body)
                        or any(row[key] != body.get(key) for key in ('project_id', 'lane_id'))):
                    raise LaneError('FEDERATION_INTEGRITY', 'A mini-brain content identity changed.')
            heads = {}
            for row in records['federation_lane_heads']:
                mini = minis.get(row['mini_brain_id'])
                if not mini or mini['project_id'] != row['project_id'] or mini['lane_id'] != row['lane_id']:
                    raise LaneError('FEDERATION_INTEGRITY', 'A current lane head points to a different member or lane.')
                heads.setdefault(row['project_id'], {})[row['lane_id']] = row['mini_brain_id']
            for row in records['federation_members']:
                member = checked(row)
                original = connection.execute('SELECT * FROM federation_member_history WHERE project_id=? AND version=?',
                    (row['project_id'], row['version'])).fetchone()
                if (original is None or checked(original) != member
                        or member.get('project_id') != row['project_id'] or member.get('version') != row['version']
                        or member['lane_heads'] != heads.get(row['project_id'], {})):
                    raise LaneError('FEDERATION_INTEGRITY', 'A member differs from its historical registration or current heads.')
            history_versions = {}
            for row in records['federation_member_history']:
                body = checked(row)
                try:
                    lane_refs = [minis[key] for _, key in sorted(body['lane_heads'].items())]
                    core = {key: body[key] for key in ('project_id', 'project_root_identity_sha256', 'root_pv')}
                    core['lanes'] = [{**item, 'mini_brain_id': 'mini_' + digest(item)} for item in lane_refs]
                    valid = (body['project_id'] == row['project_id'] and body['version'] == row['version']
                        and body['project_id'] != self.project.project_id and digest(core) == body['snapshot_sha256']
                        and all(item['project_id'] == body['project_id'] for item in lane_refs))
                except (KeyError, ValueError, TypeError):
                    valid = False
                if not valid:
                    raise LaneError('FEDERATION_INTEGRITY', 'A historical member snapshot differs from its exact lane hashes.')
                history_versions.setdefault(row['project_id'], []).append(row['version'])
            for versions in history_versions.values():
                if sorted(versions) != list(range(1, len(versions) + 1)):
                    raise LaneError('FEDERATION_INTEGRITY', 'Member registration history has a version gap.')
            grants = {row['grant_id']: checked(row) for row in records['federation_grants']}
            for grant_id, body in grants.items():
                validate_grant(body, grant_id, self._federation_id)
                if (body['grant_id'] != grant_id or body['federation_id'] != self._federation_id
                        or body['source_mini_brain_id'] not in minis or body['target_mini_brain_id'] not in minis
                        or minis[body['source_mini_brain_id']]['project_id'] == minis[body['target_mini_brain_id']]['project_id']):
                    raise LaneError('FEDERATION_INTEGRITY', 'A grant differs from its distinct registered member endpoints.')
            for row in records['federation_revocations']:
                if row['grant_id'] not in grants or checked(row)['grant_id'] != row['grant_id']:
                    raise LaneError('FEDERATION_INTEGRITY', 'A revocation differs from its original grant.')
            for row in records['federation_links']:
                if time.monotonic() >= deadline:
                    raise LaneError('FEDERATION_VERIFY_TIMEOUT', 'Use a smaller bounded federation verification scope.')
                body = checked(row)
                grant = grants.get(row['grant_id'])
                core = {key: body[key] for key in ('source_mini_brain_id', 'target_mini_brain_id', 'relation', 'explicit_grant_sha256', 'evidence')}
                if (grant is None or row['edge_id'] != 'uedge_' + digest(core) or body['edge_id'] != row['edge_id']
                        or body['grant_id'] != row['grant_id'] or body['explicit_grant_sha256'] != digest(grant)
                        or any(body[key] != grant[key] for key in ('source_mini_brain_id', 'target_mini_brain_id', 'relation'))):
                    raise LaneError('FEDERATION_INTEGRITY', 'An edge differs from its hash or explicit grant.')
                FederationLink(grant_id=body['grant_id'], evidence=body['evidence'])
        return self.result('verified', {'records_verified': request.max_records - remaining,
            'verification_scope': 'recorded_federation_integrity', 'member_current_state_verified': False})


def register_federation_actions(engine):
    def owner(context, *, write=False):
        return UniverseFederation(engine.directory.open(context.project_id, write=write))

    def authorize_members(context, member_ids):
        reader = LaneReader(engine)
        for project_id in sorted(set(member_ids)):
            if project_id == context.project_id:
                raise LaneError('FEDERATION_MEMBER_SCOPE', 'The coordinator must be separate from every member project.')
            reader._context(context, project_id)

    def mutate(context, request, operation):
        federation = owner(context, write=True)
        with engine.project_work.mutation(federation.project) as lease:
            if context.authorize:
                context.authorize('write')
            return operation(federation, lease)

    def register(context, request):
        federation = owner(context, write=True)
        federation.identity()
        authorize_members(context, [request.target_project_id])
        member = engine.directory.open(request.target_project_id)
        snapshot = inspect_project(member, request.inspection)
        if snapshot.snapshot_sha256 != request.expected_snapshot_sha256:
            raise LaneError('FEDERATION_SNAPSHOT_CHANGED', 'Inspect and select the member current snapshot.')
        prior = federation.member(member.project_id)
        authorize_members(context, [member.project_id])
        if prior and prior['snapshot_sha256'] == snapshot.snapshot_sha256:
            # Do not acquire a writer or emit a receipt for unchanged content.
            return federation.result('reused', {'member': prior, 'changed_lane_ids': [], 'removed_lane_ids': []})
        with engine.project_work.mutation(federation.project) as lease:
            if context.authorize:
                context.authorize('write')
            authorize_members(context, [member.project_id])
            current = inspect_project(engine.directory.open(member.project_id), request.inspection)
            return federation.register(request, current, lease, context.client_id)

    def grant(context, request):
        def operation(federation, lease):
            bodies = federation.endpoints(request.source_mini_brain_id, request.target_mini_brain_id)
            authorize_members(context, [body['project_id'] for body in bodies])
            return federation.grant(request, lease, context.client_id)
        return mutate(context, request, operation)

    def link(context, request):
        federation = owner(context, write=True)
        grant_body = federation.grant_record(request.grant_id)
        bodies = federation.endpoints(grant_body['source_mini_brain_id'], grant_body['target_mini_brain_id'])
        authorize_members(context, [item['project_id'] for item in bodies])
        existing = federation.existing_link(request)
        if existing:
            return federation.result('reused', existing)
        def operation(federation, lease):
            body = federation.grant_record(request.grant_id)
            bodies = federation.endpoints(body['source_mini_brain_id'], body['target_mini_brain_id'])
            authorize_members(context, [item['project_id'] for item in bodies])
            return federation.link(request, lease)
        return mutate(context, request, operation)

    def create(context, request):
        federation = owner(context, write=True)
        try:
            prior = federation.identity()
        except LaneError as error:
            if error.code != 'FEDERATION_NOT_SELECTED':
                raise
        else:
            if prior['name'] != request.name:
                raise LaneError('FEDERATION_EXISTS', 'The coordinator already owns a different named federation.')
            return federation.result('reused', prior)
        return mutate(context, request, lambda federation, lease: federation.create(request, lease, context.client_id))

    def revoke(context, request):
        federation = owner(context, write=True)
        federation.grant_record(request.grant_id, require_active=False)
        with federation.store.connection(read_only=True) as connection:
            prior = connection.execute('SELECT * FROM federation_revocations WHERE grant_id=?', (request.grant_id,)).fetchone()
        if prior:
            return federation.result('reused', checked(prior))
        return mutate(context, request, lambda federation, lease: federation.revoke(request, lease, context.client_id))

    actions = (
        ('bigger_universe_create', 'Create an explicitly selected federation in a separate coordinator project.', FederationCreate,
         create),
        ('bigger_universe_register', 'Capture verified member lane hashes, reuse unchanged content and preserve historical heads.', FederationRegister, register),
        ('bigger_universe_grant', 'Issue an explicit expiring grant for one relation between two authorized member references.', FederationGrant, grant),
        ('bigger_universe_revoke', 'Revoke a federation edge grant while retaining its grant and link history.', FederationRevoke,
         revoke),
        ('bigger_universe_link', 'Append a hash-only cross-project edge under its exact active grant and current member read access.', FederationLink, link),
    )
    for name, description, model, handler in actions:
        engine.registry.register(ActionSpec(name, description, model, FederationResult, handler,
            permission='write', profile='universe', workflow='bigger-universe', mutates=True))
    engine.registry.register(ActionSpec('bigger_universe_read', 'Read bounded historical federation hash references without opening member data.',
        FederationRead, FederationResult, lambda context, request: owner(context).read(request),
        profile='universe', workflow='bigger-universe', queryable_in_delta=True, studio_read=True))
    engine.registry.register(ActionSpec('bigger_universe_verify', 'Verify bounded federation identities, historical heads and explicit grant links.',
        FederationVerify, FederationResult, lambda context, request: owner(context).verify(request),
        profile='universe', workflow='bigger-universe', queryable_in_delta=True, studio_read=True))
    register_federation_view(engine)


def federation_view_head(project):
    result = {}
    with project.lane('universe').connection(read_only=True) as connection:
        for table in ('federation_identity', 'federation_members', 'federation_mini_brains',
                      'federation_grants', 'federation_revocations', 'federation_links'):
            exists = connection.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (table,)).fetchone()
            rows = [row[0] for row in connection.execute('SELECT digest FROM ' + table + ' ORDER BY digest LIMIT 10001')] if exists else []
            if len(rows) > 10000:
                raise LaneError('VIEW_SOURCE_BUDGET', 'Use bounded federation pages for a federation larger than this graph export budget.')
            result[table] = {'records': len(rows), 'digest': digest(rows)}
    return result


def federation_view(project, scope):
    from .lane_contract import ViewGraph
    graph = ViewGraph(project.project_id, scope)
    federation = UniverseFederation(project)
    try:
        identity = federation.identity()
    except LaneError as error:
        if error.code == 'FEDERATION_NOT_SELECTED':
            return graph.result()
        raise
    root = graph.node('federation', identity['federation_id'], identity['name'])
    with federation.store.connection(read_only=True) as connection:
        members = connection.execute('SELECT * FROM federation_members ORDER BY project_id LIMIT ?', (scope.node_limit + 1,)).fetchall()
        selected = {}
        for row in members[:scope.node_limit]:
            body = checked(row)
            selected[row['project_id']] = graph.node('federation_member', row['project_id'], row['project_id'],
                locator={'snapshot_sha256': body['snapshot_sha256'], 'root_pv': body['root_pv'], 'version': body['version']})
            graph.edge(root, selected[row['project_id']], 'REGISTERS_PROJECT')
        graph.truncated |= len(members) > scope.node_limit
        query = ('SELECT m.* FROM federation_mini_brains m ' + ('' if scope.include_history else
            'JOIN federation_lane_heads h ON m.mini_brain_id=h.mini_brain_id ')
            + 'ORDER BY m.mini_brain_id LIMIT ?')
        rows = connection.execute(query, (scope.node_limit + 1,)).fetchall()
        minis = {}
        for row in rows[:scope.node_limit]:
            body = checked(row)
            if row['mini_brain_id'] != 'mini_' + digest(body):
                raise LaneError('FEDERATION_INTEGRITY', 'A graph mini-brain differs from its recorded content identity.')
            node = graph.node('mini_brain', row['mini_brain_id'], row['lane_id'], locator=body)
            minis[row['mini_brain_id']] = node
            graph.edge(selected.get(row['project_id']), node, 'REFERENCES_LANE')
        graph.truncated |= len(rows) > scope.node_limit
        edges = connection.execute('SELECT * FROM federation_links ORDER BY edge_id LIMIT ?', (scope.edge_limit + 1,)).fetchall()
        for row in edges[:scope.edge_limit]:
            body = checked(row)
            revoked = connection.execute('SELECT 1 FROM federation_revocations WHERE grant_id=?', (row['grant_id'],)).fetchone()
            # Distinct edge nodes preserve multiple relations between the same
            # mini-brains. A historical edge survives its mutation grant.
            edge = graph.node('federation_edge', row['edge_id'], body['relation'], locator={
                    'edge_id': row['edge_id'], 'relation': body['relation'], 'evidence': body['evidence'],
                    'explicit_grant_sha256': body['explicit_grant_sha256'], 'grant_revoked': bool(revoked),
                    'grant_current_authority_claimed': False})
            graph.edge(minis.get(body['source_mini_brain_id']), edge, 'HASH_SOURCE', provenance='explicit_federation_grant')
            graph.edge(edge, minis.get(body['target_mini_brain_id']), 'HASH_TARGET', provenance='explicit_federation_grant')
        graph.truncated |= len(edges) > scope.edge_limit
    return graph.result()


def register_federation_view(engine):
    from .lane_contract import LaneView
    engine.registry.register_view(LaneView('universe.federation', 'universe', 'authorities/universe',
        'Distinct registered projects, hash-only lane references and explicitly granted historical cross-project edges.',
        federation_view, ('federation',), FEDERATION_MIGRATIONS, 'bigger_universe.mmd', 'bigger_universe.dot',
        supports_history=True, node_kinds=('federation', 'federation_member', 'mini_brain', 'federation_edge'),
        edge_kinds=('REGISTERS_PROJECT', 'REFERENCES_LANE', 'HASH_SOURCE', 'HASH_TARGET'), head_reader=federation_view_head))
