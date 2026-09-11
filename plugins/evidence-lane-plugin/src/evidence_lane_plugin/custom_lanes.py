"""Sources-owned, immutable adapter registrations for separate Custom instances."""
from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import Field, model_validator

from .document_parsers import digest
from .errors import LaneError
from .hashing import canonical_json_bytes
from .lanes import CUSTOM_INSTANCE_PATTERN, MAX_CUSTOM_INSTANCES, get_lane, is_named_custom_lane
from .migrations import Migration, apply_migrations, read_compatibility
from .registry import ActionSpec, Contract
from .sector_evidence_contracts import DIGEST, PARSER_NAMES, parser_for
from .storage import now, project_snapshot

MIGRATIONS = (Migration('customlanes', 1, 'Immutable named Custom adapter contracts in Sources', (
    '''CREATE TABLE customlanes_contracts(contract_sha256 TEXT PRIMARY KEY,lane_id TEXT NOT NULL,
        previous_contract TEXT REFERENCES customlanes_contracts(contract_sha256),
        contract_json TEXT NOT NULL CHECK(json_valid(contract_json)),created_at TEXT NOT NULL) STRICT''',
    '''CREATE TABLE customlanes_current(lane_id TEXT PRIMARY KEY,
        contract_sha256 TEXT NOT NULL REFERENCES customlanes_contracts(contract_sha256)) STRICT''',
)),)


class Adapter(Contract):
    parser: PARSER_NAMES
    extensions: list[Annotated[str, Field(pattern=r'^\.[a-z0-9]{1,15}$')]] = Field(min_length=1, max_length=32)
    max_file_bytes: int = Field(default=1_048_576, ge=1, le=8_388_608)
    sqlite_tables: list[str] = Field(default_factory=list, max_length=32)
    sqlite_rows: int = Field(default=100, ge=0, le=200)

    @model_validator(mode='after')
    def explicit(self):
        from .sector_evidence_contracts import Index
        if self.parser == 'auto' or len(set(self.extensions)) != len(self.extensions):
            raise ValueError('Select one explicit retained parser and distinct extensions')
        for suffix in self.extensions:
            Index(lane_id='custom', filename='source' + suffix, parser=self.parser,
                  sqlite_tables=self.sqlite_tables, sqlite_rows=self.sqlite_rows)
        return self


class Configure(Contract):
    lane_id: str = Field(pattern='^' + CUSTOM_INSTANCE_PATTERN + '$')
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=1000)
    adapter: Adapter
    expected_contract: str | None = Field(default=None, pattern=DIGEST)


class ReadRegistrations(Contract):
    lane_id: str | None = Field(default=None, pattern='^' + CUSTOM_INSTANCE_PATTERN + '$')
    contract_sha256: str | None = Field(default=None, pattern=DIGEST)
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=20, ge=1, le=100)
    max_bytes: int = Field(default=65_536, ge=2048, le=262_144)


def records(store, request):
    lane = store.lane('sources')
    state = read_compatibility(lane, MIGRATIONS)
    if state[0]['status'] == 'not_initialized':
        return {'rows': [], 'next_offset': None}
    where, values = [], []
    if request.lane_id:
        where.append('c.lane_id=?')
        values.append(request.lane_id)
    if request.contract_sha256:
        where.append('c.contract_sha256=?')
        values.append(request.contract_sha256)
    else:
        where.append('h.contract_sha256=c.contract_sha256')
    with lane.connection(read_only=True) as connection:
        rows = connection.execute('SELECT c.* FROM customlanes_contracts c LEFT JOIN customlanes_current h USING(lane_id)'
            + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY c.lane_id,c.contract_sha256 LIMIT ? OFFSET ?',
            (*values, request.limit + 1, request.offset)).fetchall()
    result, size = [], 0
    for row in rows:
        body = json.loads(row['contract_json'])
        if (digest(canonical_json_bytes(body)) != row['contract_sha256']
                or body['project_id'] != store.project_id or body['lane_id'] != row['lane_id']
                or body['previous_contract'] != row['previous_contract']):
            raise LaneError('CUSTOM_LANE_CONTRACT_INTEGRITY', 'The named adapter record differs from its immutable identity.')
        value = {'contract_sha256': row['contract_sha256'], **body}
        length = len(canonical_json_bytes(value))
        if len(result) == request.limit or size + length > request.max_bytes:
            if not result:
                raise LaneError('CUSTOM_LANE_READ_BUDGET', 'Increase the bound to read one complete adapter contract.')
            break
        size += length
        result.append(value)
    return {'rows': result, 'next_offset': request.offset + len(result) if len(rows) > len(result) else None}


def registered_lane_ids(store):
    """Bounded project registrations; template definitions are not live instances."""
    lane = store.lane('sources')
    if read_compatibility(lane, MIGRATIONS)[0]['status'] == 'not_initialized':
        return []
    with lane.connection(read_only=True) as connection:
        values = [row[0] for row in connection.execute('SELECT lane_id FROM customlanes_current ORDER BY lane_id LIMIT ?',
            (MAX_CUSTOM_INSTANCES + 1,))]
    if len(values) > MAX_CUSTOM_INSTANCES or not all(is_named_custom_lane(value) for value in values):
        raise LaneError('CUSTOM_LANE_CATALOG_INTEGRITY', 'The project Custom registration catalog is invalid or exceeds its bound.')
    return values


def selected_adapter(store, request):
    from .sector_evidence_profile import parser_contract
    if not is_named_custom_lane(request.lane_id):
        if request.adapter_contract is not None:
            raise LaneError('CUSTOM_LANE_SELECTION_INVALID', 'Adapter contracts select named Custom instances only.')
        return None
    rows = records(store, ReadRegistrations(lane_id=request.lane_id))['rows']
    if not rows or rows[0]['contract_sha256'] != request.adapter_contract:
        raise LaneError('CUSTOM_LANE_CONTRACT_CHANGED', 'Bind this task to the current registered Custom adapter contract.')
    adapter = Adapter.model_validate(rows[0]['adapter'])
    if rows[0]['parser_contract_sha256'] != parser_contract(adapter.parser):
        raise LaneError('CUSTOM_LANE_ADAPTER_CHANGED', 'Version the adapter against the current installed parser before preparing new work.')
    if (PurePosixPath(request.filename).suffix.lower() not in adapter.extensions
            or request.parser != adapter.parser or parser_for(request.filename, request.parser) != adapter.parser
            or request.max_file_bytes > adapter.max_file_bytes
            or not set(request.sqlite_tables) <= set(adapter.sqlite_tables)
            or request.sqlite_rows > adapter.sqlite_rows):
        raise LaneError('CUSTOM_LANE_ADAPTER_SCOPE', 'Keep the source, parser and extraction bounds within the selected adapter.')
    return rows[0]['contract_sha256']


def register_custom_lane_actions(engine):
    from .source_intake import SourceOperationResult, _source_action_result

    def configure(context, request):
        from .sector_evidence_profile import parser_contract
        from .sector_evidence_schema import migrations
        store = engine.directory.open(context.project_id, write=True)
        body = {'schema': 'evidence-lane.custom-instance-adapter.v4', 'project_id': store.project_id,
            'lane_id': request.lane_id, 'display_name': request.display_name, 'description': request.description,
            'adapter': request.adapter.model_dump(mode='json'), 'previous_contract': request.expected_contract,
            'parser_contract_sha256': parser_contract(request.adapter.parser),
            'database_path': get_lane(request.lane_id).database_relative_path,
            'external_code_loaded': False, 'source_payload_read': False}
        identity = digest(canonical_json_bytes(body))
        with engine.project_work.mutation(store) as lease:
            found = records(store, ReadRegistrations(lane_id=request.lane_id))['rows']
            current = found[0]['contract_sha256'] if found else None
            if current == identity:
                return _source_action_result(store, 'custom_lane_configure', {'contract_sha256': identity, **body})
            if current != request.expected_contract:
                raise LaneError('CUSTOM_LANE_CONTRACT_CHANGED', 'Select the exact previous adapter contract before replacing it.')
            if current is None:
                with store.lane('sources').connection(read_only=True) as connection:
                    present = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='customlanes_current'").fetchone()
                    if present and connection.execute('SELECT COUNT(*) FROM customlanes_current').fetchone()[0] >= MAX_CUSTOM_INSTANCES:
                        raise LaneError('CUSTOM_LANE_COUNT_BUDGET', 'The project reached its bounded Custom instance registration capacity.')
            with lease.coordinated_transaction(['sources', request.lane_id]):
                context.authorize('write')
                apply_migrations(store.lane('sources'), MIGRATIONS, writer=lease)
                apply_migrations(store.lane(request.lane_id), migrations(request.lane_id), writer=lease)
                with store.lane('sources').transaction() as connection:
                    connection.execute('INSERT INTO customlanes_contracts VALUES(?,?,?,?,?)',
                        (identity, request.lane_id, request.expected_contract, canonical_json_bytes(body).decode(), now()))
                    connection.execute('INSERT INTO customlanes_current VALUES(?,?) ON CONFLICT(lane_id) DO UPDATE SET contract_sha256=excluded.contract_sha256',
                        (request.lane_id, identity))
                store.append_receipt('custom_lane_configured', {'lane_id': request.lane_id, 'contract_sha256': identity,
                    'previous_contract': request.expected_contract, 'client_id': context.client_id,
                    'request_id': context.request_id, 'native_task_id': context.native_task_id})
        return _source_action_result(store, 'custom_lane_configure', {'contract_sha256': identity, **body})

    def read(context, request):
        store = engine.directory.open(context.project_id)
        with project_snapshot(store.root):
            return _source_action_result(store, 'custom_lanes_read', records(store, request))

    engine.registry.register(ActionSpec('custom_lane_configure',
        'Register or version one named Custom lane and its bounded retained-parser adapter; allocate its own database and files.',
        Configure, SourceOperationResult, configure, permission='write', mutates=True, profile='sources', workflow='source-intake'))
    engine.registry.register(ActionSpec('custom_lanes_read',
        'Read current or exact historical Custom instance adapter contracts from Sources.',
        ReadRegistrations, SourceOperationResult, read, profile='sources', workflow='source-intake',
        studio_read=True, queryable_in_delta=True, cross_project_read=True, read_migrations=MIGRATIONS))
