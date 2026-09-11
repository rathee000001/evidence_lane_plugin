"""Exact inherited source routes, consumed by one attributed registration.

The former session grant/candidate handoff is replaced by a single coordinated
Sources publication. Route receipts select immutable source batches; they are
never a global default for unrelated work or permission to read another path.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated

from pydantic import Field, model_validator

from .bounded_io import DEFAULT_MAX_AGGREGATE_BYTES, DEFAULT_MAX_FILE_COUNT
from .errors import LaneError
from .migrations import Migration, apply_migrations, read_compatibility
from .registry import Contract
from .storage import json_text, now, reject_links

ROUTE_BYTES = 2_097_152
ROUTE_MIGRATIONS = (Migration('sourceroutes', 1, 'Immutable source routing and one-operation consumption', (
    """CREATE TABLE sourceroutes_receipts (
       route_id TEXT PRIMARY KEY REFERENCES objects(digest),
       batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
       parent_route_id TEXT REFERENCES sourceroutes_receipts(route_id),
       created_at TEXT NOT NULL)""",
    """CREATE TABLE sourceroutes_requests (
       request_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, action TEXT NOT NULL,
       request_digest TEXT NOT NULL, route_id TEXT NOT NULL REFERENCES sourceroutes_receipts(route_id),
       result_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL)""",
)), Migration('sourceroutes', 2, 'Attributed immutable source task preparations', (
    """CREATE TABLE sourceroutes_preparations (
       request_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, request_digest TEXT NOT NULL,
       preparation_id TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL)""",
)),)


class SourceRoutesRead(Contract):
    route_id: str = Field(pattern=r'^[0-9a-f]{64}$')


class SourceRouteSelection(SourceRoutesRead):
    occurrence_ordinals: list[Annotated[int, Field(ge=1, le=256)]] = Field(min_length=1, max_length=32)

    @model_validator(mode='after')
    def distinct(self):
        if len(set(self.occurrence_ordinals)) != len(self.occurrence_ordinals):
            raise ValueError('Select distinct source occurrences')
        return self


def digest(value):
    return hashlib.sha256(json_text(value).encode()).hexdigest()


def _table(connection, name):
    return connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (name,)).fetchone()


def _read(lane, object_id):
    with lane.connection(read_only=True) as connection:
        row = connection.execute('SELECT size_bytes FROM objects WHERE digest=?', (object_id,)).fetchone()
    if row is None or not 1 <= row['size_bytes'] <= ROUTE_BYTES:
        raise LaneError('SOURCE_ROUTE_BUDGET', 'The routing receipt is absent or exceeds its read budget.')
    try:
        with lane.object_path(object_id).open('rb') as stream:
            raw = stream.read(ROUTE_BYTES + 1)
        if len(raw) != row['size_bytes'] or hashlib.sha256(raw).hexdigest() != object_id:
            raise ValueError('object hash')
        return json.loads(raw)
    except (OSError, ValueError) as error:
        raise LaneError('SOURCE_ROUTE_INTEGRITY', 'The routing receipt bytes failed integrity verification.') from error


def batch_routes(store, batch_id, *, publishing=False):
    """Read only the bounded routing columns; never load unrelated graph data."""
    from .source_authority import SOURCES_MIGRATIONS
    lane = store.lane('sources')
    if not publishing:
        read_compatibility(store, SOURCES_MIGRATIONS)
    with lane.connection(read_only=True) as connection:
        if not _table(connection, 'intake_batch'):
            raise LaneError('SOURCE_AUTHORITY_BATCH_MISSING', 'Register the selected source batch first.')
        batch = connection.execute('SELECT batch_sha256,source_count FROM intake_batch WHERE batch_id=?', (batch_id,)).fetchone()
        if batch is None:
            raise LaneError('SOURCE_AUTHORITY_BATCH_MISSING', 'Select a registered source batch.')
        if not 1 <= batch['source_count'] <= 256:
            raise LaneError('SOURCE_ROUTE_BUDGET', 'Select a batch of at most 256 source occurrences.')
        budget = connection.execute('SELECT COUNT(*),COALESCE(SUM(length(o.supplied_pointer)+length(o.lane_id)'
            '+length(o.object_id)+length(s.resolved_pointer)+length(s.kind)+length(s.identity_sha256)'
            '+COALESCE(length(s.byte_sha256),0)+COALESCE(length(s.content_merkle_sha256),0)'
            '+COALESCE(length(s.member_path_size_sha256),0)),0) '
            'FROM source_occurrence o JOIN source_object s USING(object_id) WHERE o.batch_id=?', (batch_id,)).fetchone()
        if budget[0] != batch['source_count'] or budget[1] > ROUTE_BYTES:
            raise LaneError('SOURCE_ROUTE_BUDGET', 'The source occurrence metadata exceeds its exact count or byte budget.')
        rows = [dict(row) for row in connection.execute(
            'SELECT o.ordinal,o.supplied_pointer,o.lane_id,o.object_id,s.resolved_pointer,s.kind,'
            's.identity_sha256,s.byte_sha256,s.size_bytes,s.included_member_count,'
            's.content_merkle_sha256,s.member_path_size_sha256 '
            'FROM source_occurrence o JOIN source_object s USING(object_id) '
            'WHERE o.batch_id=? ORDER BY o.ordinal LIMIT 257', (batch_id,))]
    if len(rows) != batch['source_count'] or [row['ordinal'] for row in rows] != list(range(1, len(rows) + 1)):
        raise LaneError('SOURCE_ROUTE_INTEGRITY', 'The source batch has inconsistent ordered occurrences.')
    from .lanes import get_lane
    for row in rows:
        lane_definition = get_lane(row['lane_id'])
        if lane_definition.kind != 'sector' or lane_definition.canonical_lane_id != row['lane_id']:
            raise LaneError('SOURCE_ROUTE_INTEGRITY', 'A registered route does not name a retained canonical sector.')
    if len(json_text(rows).encode()) > ROUTE_BYTES:
        raise LaneError('SOURCE_ROUTE_BUDGET', 'The source routing metadata exceeds its byte budget.')
    return batch['batch_sha256'], rows


def load_route(store, route_id):
    lane = store.lane('sources')
    read_compatibility(store, ROUTE_MIGRATIONS)
    with lane.connection(read_only=True) as connection:
        row = connection.execute('SELECT * FROM sourceroutes_receipts WHERE route_id=?', (route_id,)).fetchone() if _table(connection, 'sourceroutes_receipts') else None
    if row is None:
        raise LaneError('SOURCE_ROUTE_MISSING', 'Select an exact routing receipt from this project.')
    try:
        body = _read(lane, route_id)
        if (digest(body) != route_id or body['project_id'] != store.project_id
                or body['batch_id'] != row['batch_id'] or body['parent_route_id'] != row['parent_route_id']):
            raise ValueError('route binding')
        batch_digest, routes = batch_routes(store, body['batch_id'])
        if body['batch_sha256'] != batch_digest or body['routes'] != routes:
            raise ValueError('source binding')
    except (ValueError, KeyError, TypeError) as error:
        raise LaneError('SOURCE_ROUTE_INTEGRITY', 'The routing receipt differs from its immutable source batch.') from error
    return body


def inherited_routes(store, sources, overrides, parent_route_id):
    parent = load_route(store, parent_route_id) if parent_route_id else None
    prior = {row['supplied_pointer']: row['lane_id'] for row in parent['routes']} if parent else {}
    exact = [value.strip() for value in sources]
    inherited = {source: prior[source] for source in exact if source in prior and source not in overrides}
    return {**inherited, **overrides}, {
        'parent_route_id': parent_route_id,
        'parent_batch_id': parent['batch_id'] if parent else None,
        'inherited_sources': list(inherited),
        'explicit_sources': sorted(overrides),
        'omitted_parent_sources': sorted(set(prior) - set(exact)),
        'inheritance_key': 'exact_supplied_pointer',
    }


def replay_registration(store, context, action, values):
    lane = store.lane('sources')
    read_compatibility(store, ROUTE_MIGRATIONS)
    with lane.connection(read_only=True) as connection:
        row = connection.execute('SELECT * FROM sourceroutes_requests WHERE request_id=?', (context.request_id,)).fetchone() if _table(connection, 'sourceroutes_requests') else None
    if row is None:
        return None
    if row['client_id'] != context.client_id or row['action'] != action or row['request_digest'] != digest(values):
        raise LaneError('SOURCE_ROUTE_REQUEST_CONFLICT', 'This source request identity already binds another operation.')
    load_route(store, row['route_id'])
    result = _read(lane, row['result_object'])
    if (digest(result) != row['result_object'] or result['source_routes']['route_id'] != row['route_id']
            or result['source_routes']['request_id'] != context.request_id
            or result['source_routes']['client_id'] != context.client_id):
        raise LaneError('SOURCE_ROUTE_INTEGRITY', 'The source registration receipt does not match its recorded request.')
    return result


def publish_batch_route(store, batch_id, inheritance, lease):
    """Sources-owned route publication within the caller's coordinated write."""
    lane = store.lane('sources')
    apply_migrations(store, ROUTE_MIGRATIONS, writer=lease)
    batch_digest, routes = batch_routes(store, batch_id, publishing=True)
    body = {'schema': 'evidence-lane.source-route-receipt.v4', 'project_id': store.project_id,
        'batch_id': batch_id, 'batch_sha256': batch_digest, 'routes': routes, **inheritance}
    route_id = lane.put_object(json_text(body).encode(), limit=ROUTE_BYTES)
    stamp = now()
    with lane.transaction() as connection:
        connection.execute('INSERT OR IGNORE INTO sourceroutes_receipts VALUES(?,?,?,?)',
            (route_id, batch_id, inheritance['parent_route_id'], stamp))
    return route_id


def publish_routes(store, context, action, values, result, inheritance, lease):
    """Called inside the same Sources transaction as the batch being frozen."""
    if not context.request_id:
        raise LaneError('SOURCE_ROUTE_REQUEST_REQUIRED', 'An attributed source registration requires its request identity.')
    lane = store.lane('sources')
    batch_id = result['source_authority']['batch_id']
    route_id = publish_batch_route(store, batch_id, inheritance, lease)
    stamp = now()
    with lane.transaction() as connection:
        result['source_routes'] = {'route_id': route_id, 'batch_id': batch_id, **inheritance,
            'client_id': context.client_id, 'request_id': context.request_id, 'action': action,
            'consumed_by': 'this_source_registration', 'consumed_at': stamp,
            'observation_scope': 'at_registration_commit', 'global_route_changed': False}
        result_object = lane.put_object(json_text(result).encode(), limit=ROUTE_BYTES)
        connection.execute('INSERT INTO sourceroutes_requests VALUES(?,?,?,?,?,?,?)',
            (context.request_id, context.client_id, action, digest(values), route_id, result_object, stamp))
    store.append_receipt('source_sdk_action', {'action': action, 'client_id': context.client_id,
        'request_id': context.request_id, 'request_digest': digest(values), 'route_id': route_id,
        'source_lane': 'sources', 'batch_id': batch_id, 'consumed_by': 'this_source_registration'})
    return result


class SourceRouteGuard:
    """Restrict a selected consumer to frozen inputs inside its Plan path grant.

    The parser continues to own format fidelity and derived dependency evidence.
    This guard verifies the registered input bytes and routes, not arbitrary
    filesystem dependencies inferred by a document or BI parser.
    """
    MAX_FILES = 512
    MAX_BYTES = 33_554_432
    # Sources may describe more files than one parser operation consumes.
    # Bound the complete metadata observation separately; keep the original
    # directory hashes and the smaller actual-input limits below.
    MAX_METADATA_FILES = DEFAULT_MAX_FILE_COUNT
    MAX_METADATA_BYTES = 8_388_608

    def __init__(self, guard, selection, arguments, *, consumer_spec=None):
        self.guard, self.selection = guard, selection
        spec, store = consumer_spec or guard.spec, guard.store
        self.lane_id = arguments.get('lane_id')
        from .lanes import lane_family
        if not spec.source_lanes or lane_family(self.lane_id) not in spec.source_lanes:
            raise LaneError('SOURCE_ROUTE_CONSUMER_UNSUPPORTED', 'Select a registry-declared consumer for this source lane.')
        self.receipt = load_route(store, selection.route_id)
        self.routes = self.receipt['routes']
        self.selected = [row for row in self.routes if row['ordinal'] in selection.occurrence_ordinals]
        if len(self.selected) != len(selection.occurrence_ordinals):
            raise LaneError('SOURCE_ROUTE_OCCURRENCE_MISSING', 'Select existing occurrences from the exact routing receipt.')
        if any(row['lane_id'] != self.lane_id for row in self.selected):
            raise LaneError('SOURCE_ROUTE_LANE_MISMATCH', 'The selected source occurrences belong to another sector.')
        self.scopes = []
        for field in spec.path_fields:
            values = arguments[field] if isinstance(arguments[field], list) else [arguments[field]]
            self.scopes.extend(guard.path(value) for value in values)
        self.paths = {}
        for row in self.routes:
            path = Path(row['resolved_pointer'])
            if row['kind'] in {'file', 'zip', 'directory'} and path.is_absolute():
                self.paths[row['ordinal']] = path
        if any(row['ordinal'] not in self.paths for row in self.selected):
            raise LaneError('SOURCE_ROUTE_LOCAL_INPUT_REQUIRED', 'This consumer needs granted local file or directory sources.')
        for row in self.selected:
            source = self.paths[row['ordinal']]
            if not source.is_relative_to(store.source_root):
                raise LaneError('SOURCE_ROUTE_PATH_MISMATCH', 'The routed source is outside this project source root.')
            if not any(source.is_relative_to(scope) or (row['kind'] == 'directory' and scope.is_relative_to(source)) for scope in self.scopes):
                raise LaneError('SOURCE_ROUTE_PATH_MISMATCH', 'Every selected source occurrence must intersect this operation scope.')
        self.expected = {}
        metadata_files = metadata_bytes = 0
        lane = store.lane('sources')
        from .source_selection import registered_directory_selection
        self.directory_selections = {row['ordinal']: registered_directory_selection(store, row['object_id'])
            for row in self.selected if row['kind'] == 'directory'}
        for row in self.selected:
            source = self.paths[row['ordinal']]
            if row['kind'] in {'file', 'zip'}:
                members = [{'member_path': None, 'sha256': row['byte_sha256'], 'size_bytes': row['size_bytes']}]
            else:
                with lane.connection(read_only=True) as connection:
                    totals = connection.execute('SELECT COUNT(*),COALESCE(SUM(size_bytes),0),'
                        'COALESCE(SUM(length(CAST(member_path AS BLOB))+length(CAST(sha256 AS BLOB))'
                        '+length(CAST(member_kind AS BLOB))+length(CAST(policy_state AS BLOB))'
                        '+length(CAST(policy_reason AS BLOB))),0) '
                        'FROM source_member WHERE object_id=?', (row['object_id'],)).fetchone()
                    metadata_files += totals[0]
                    metadata_bytes += totals[2]
                    if (metadata_files > self.MAX_METADATA_FILES or metadata_bytes > self.MAX_METADATA_BYTES
                            or not 0 <= totals[1] <= DEFAULT_MAX_AGGREGATE_BYTES):
                        raise LaneError('SOURCE_ROUTE_METADATA_BUDGET', 'Select source occurrences within the complete directory metadata budget.')
                    members = [dict(item) for item in connection.execute(
                        'SELECT member_path,sha256,size_bytes,member_kind,policy_state,policy_reason '
                        'FROM source_member WHERE object_id=? ORDER BY rowid LIMIT ?',
                        (row['object_id'], self.MAX_METADATA_FILES + 1))]
                if (len(members) != row['included_member_count'] or len(members) != totals[0]
                        or any(type(item['size_bytes']) is not int or item['size_bytes'] < 0 for item in members)):
                    raise LaneError('SOURCE_ROUTE_INTEGRITY', 'The source directory membership differs from its registered identity.')
                from .source_authority import _member_merkle, _member_path_size_identity
                if (_member_merkle(members) != row['content_merkle_sha256']
                        or _member_path_size_identity(members) != row['member_path_size_sha256']):
                    raise LaneError('SOURCE_ROUTE_INTEGRITY', 'The directory member hashes differ from its frozen source identity.')
            for member in members:
                path = source if member['member_path'] is None else source / member['member_path']
                if not any(path.is_relative_to(scope) for scope in self.scopes) or not self._routed_here(path):
                    continue
                guard.path(str(path))
                item = {'sha256': member['sha256'], 'size_bytes': member['size_bytes']}
                if path in self.expected and self.expected[path] != item:
                    raise LaneError('SOURCE_ROUTE_AMBIGUOUS', 'Overlapping registered sources disagree about the same input bytes.')
                self.expected[path] = item
        if not self.expected:
            raise LaneError('SOURCE_ROUTE_EMPTY_SELECTION', 'The selected source routes contain no input files for this operation.')
        if (len(self.expected) > self.MAX_FILES or any(not isinstance(row['size_bytes'], int) or row['size_bytes'] < 0 for row in self.expected.values())
                or sum(row['size_bytes'] for row in self.expected.values()) > self.MAX_BYTES):
            raise LaneError('SOURCE_ROUTE_INPUT_BUDGET', 'Select at most 512 files and 32 MiB of frozen input bytes.')
        for scope in self.scopes:
            if scope.is_file() and not self.allows(scope):
                raise LaneError('SOURCE_ROUTE_PATH_MISMATCH', 'The operation source belongs to another route or occurrence.')
        self.check()

    def selected_input_paths(self):
        """Reconcile recorded modern directories, then yield their exact inputs.

        A legacy directory still needs its original consumer enumeration. For
        current captures, listing ignored trees again would contradict Sources.
        Membership is rechecked for each enumeration, including after parsing.
        """
        if any(selection is None for selection in self.directory_selections.values()):
            return None
        from .source_intake import _bounded_directory_members
        observed = set()
        for row in self.selected:
            source = self.paths[row['ordinal']]
            if row['kind'] == 'directory':
                scopes = ([source] if any(source.is_relative_to(scope) for scope in self.scopes)
                    else [scope for scope in self.scopes if scope.is_relative_to(source)])
                for scope in scopes:
                    self.guard.path(str(scope))
                selected, _ = _bounded_directory_members(source,
                    required_selection=self.directory_selections[row['ordinal']], member_scopes=scopes)
                paths = [source / name for name, _ in selected]
            else:
                paths = [source]
            observed.update(path for path in paths if any(path.is_relative_to(scope) for scope in self.scopes)
                and self._routed_here(path))
        if observed != set(self.expected):
            raise LaneError('SOURCE_ROUTE_SOURCE_CHANGED', 'The selected directory membership changed; refresh its source route before indexing.')
        return sorted(observed)

    def _routed_here(self, path):
        candidates = [row for row in self.routes if row['ordinal'] in self.paths
            and (path == self.paths[row['ordinal']] or (row['kind'] == 'directory' and path.is_relative_to(self.paths[row['ordinal']])))]
        if not candidates:
            return False
        depth = max(len(self.paths[row['ordinal']].parts) for row in candidates)
        exact = [row for row in candidates if len(self.paths[row['ordinal']].parts) == depth]
        if len({row['lane_id'] for row in exact}) != 1:
            raise LaneError('SOURCE_ROUTE_AMBIGUOUS', 'Equally specific source routes disagree about the target lane.')
        return exact[0]['lane_id'] == self.lane_id and any(row['ordinal'] in self.selection.occurrence_ordinals for row in exact)

    def allows(self, path):
        if not self._routed_here(path):
            return False
        if path not in self.expected:
            raise LaneError('SOURCE_ROUTE_SOURCE_CHANGED', 'A selected source directory gained an unregistered input file; refresh its source route first.')
        return True

    def check(self):
        from .projects import ProjectAccess
        for path, expected in self.expected.items():
            self.guard.path(str(path))
            ProjectAccess(self.guard.store).authorize(self.guard.context.client_id, 'read', path=path)
            reject_links(path, self.guard.store.source_root)
            try:
                with path.open('rb') as stream:
                    raw = stream.read(expected['size_bytes'] + 1)
                if len(raw) != expected['size_bytes'] or hashlib.sha256(raw).hexdigest().upper() != expected['sha256'].upper():
                    raise ValueError('source bytes')
            except (OSError, ValueError) as error:
                raise LaneError('SOURCE_ROUTE_SOURCE_CHANGED', 'A routed input differs from the registered bytes; register its refresh before execution.') from error

    def evidence(self):
        return {'route_id': self.selection.route_id, 'batch_id': self.receipt['batch_id'],
            'batch_sha256': self.receipt['batch_sha256'], 'occurrence_ordinals': self.selection.occurrence_ordinals,
            'lane_id': self.lane_id, 'input_file_count': len(self.expected),
            'input_bytes': sum(row['size_bytes'] for row in self.expected.values()),
            'input_scope_sha256': digest({str(path): row for path, row in sorted(self.expected.items())}),
            'scope': 'registered_operation_inputs', 'parser_dependency_closure_attested': False}


class SourceMutationRefresh:
    """Reuse the Sources capture and publication owners for one local edit.

    Full parent occurrence order and overrides are preserved. Every source root
    must be an authorized, bounded local observation before the edit. This does
    not replace frozen parent evidence or advance a global routing default.
    """
    def __init__(self, context, paths, *, parent_route_id=None, lane_id='local_code',
                 action='code_apply', max_file_bytes=1_048_576, allow_create=False, allow_archives=False):
        from .source_authority import SourceAuthoritySpec, freeze_source_authority
        self.context, self.store = context, context.execution.store
        self.parent_route_id = parent_route_id
        self.lane_id, self.action = lane_id, action
        self.allow_archives = allow_archives
        self.max_file_bytes, self.pending_paths = max_file_bytes, set()
        parent = load_route(self.store, parent_route_id) if parent_route_id else None
        if parent:
            from .source_selection import registered_directory_selection
            rows = parent['routes']
            with self.store.lane('sources').connection(read_only=True) as connection:
                size = connection.execute('SELECT COUNT(*),COALESCE(SUM(length(claim_key)+length(claim_json)),0) '
                    'FROM source_provenance WHERE batch_id=?', (parent['batch_id'],)).fetchone()
                if size[0] > 1024 or size[1] > 131_072:
                    raise LaneError('SOURCE_REFRESH_ASSERTION_BUDGET', 'Select a smaller source assertion batch for automatic refresh.')
                claims = [dict(row) for row in connection.execute('SELECT * FROM source_provenance WHERE batch_id=?',
                    (parent['batch_id'],))]
            self.specs = [SourceAuthoritySpec(row['supplied_pointer'], row['ordinal'], row['lane_id'],
                {claim['claim_key']: json.loads(claim['claim_json']) for claim in claims
                 if claim['object_id'] == row['object_id']},
                directory_selection=registered_directory_selection(self.store, row['object_id'])) for row in rows]
        else:
            rows = None
            self.specs = [SourceAuthoritySpec(str(context.execution.guard.path(path)), ordinal, lane_id)
                for ordinal, path in enumerate(paths, 1)]
        self.before = self.capture()
        frozen = []
        for spec in self.specs:
            path = Path(spec.source)
            if rows is None and allow_create and not path.exists():
                self.before.boundary(path)
                if not path.parent.is_dir():
                    raise LaneError('SOURCE_REFRESH_PARENT_MISSING', 'Select an existing destination directory.')
                self.pending_paths.add(path)
            else:
                frozen.append(freeze_source_authority(spec, capture=self.before))
        if rows and any(source.identity_sha256.lower() != row['identity_sha256'].lower()
                        for source, row in zip(frozen, rows, strict=True)):
            raise LaneError('SOURCE_ROUTE_SOURCE_CHANGED', 'A source in the selected parent route changed; refresh it before editing.')

    def capture(self):
        from .projects import ProjectAccess
        from .source_authority import SourceCaptureBudget
        execution = self.context.execution
        def authorize(path):
            execution.guard.path(str(path))
            ProjectAccess(self.store).authorize(self.context.client_id, 'read', path=path)
        return SourceCaptureBudget(authorize, execution._before_more_work,
            max_file_bytes=self.max_file_bytes, allow_archives=self.allow_archives)

    def expected_after(self, path, before_sha256, replacement):
        previous = self.before.identities.get(path)
        if (previous is None and not (path in self.pending_paths and before_sha256 is None)
                or previous is not None and previous['sha256'] != before_sha256):
            raise LaneError('SOURCE_REFRESH_EDIT_SCOPE', 'The edited file is absent from the exact observed source scope.')
        expected = dict(self.before.identities)
        expected[path] = {'size_bytes': len(replacement), 'sha256': hashlib.sha256(replacement).hexdigest()}
        if (len(expected) > self.before.max_files or len(replacement) > self.before.max_file_bytes
                or sum(row['size_bytes'] for row in expected.values()) > self.before.max_total_bytes):
            raise LaneError('SOURCE_CAPTURE_BYTE_BUDGET', 'The replacement would exceed the source refresh byte budget.')
        if path.suffix.casefold() == '.zip' and self.allow_archives:
            from .source_authority import _archive_members
            probe = self.capture()
            probe.archive_observations = dict(self.before.archive_observations)
            _archive_members(path, {}, capture=probe, content=replacement)
        return expected

    def publish(self, *, path, before_sha256, replacement, mutation_id, effect_id):
        from .source_authority import register_source_batch
        expected = self.expected_after(path, before_sha256, replacement)
        capture = self.capture()
        execution = self.context.execution
        result = register_source_batch(self.store, self.specs, capture=capture, writer=execution.lease)
        if capture.identities != expected:
            raise LaneError('SOURCE_REFRESH_UNEXPECTED_CHANGE', 'The source changed beyond the single confirmed replacement; reconcile this edit.')
        sources = [spec.source for spec in self.specs]
        _, inheritance = inherited_routes(self.store, sources, {}, self.parent_route_id)
        values = {'mutation_id': mutation_id, 'effect_id': effect_id, 'parent_route_id': self.parent_route_id,
                  'sources': sources, 'source_identity_sha256': digest({str(key): value for key, value in sorted(expected.items())})}
        refreshed = publish_routes(self.store, self.context, self.action, values,
            {'source_authority': result, 'confirmed_source_edit': values}, inheritance, execution.lease)
        receipt = self.store.append_receipt('source_refresh_after_code_edit' if self.action == 'code_apply' else 'source_refresh_after_export', {
            'project_id': self.store.project_id, 'job_id': execution.claim.job_id, 'task_id': execution.task_id,
            'plan_revision': execution.plan_revision, **values,
            'action': self.action, 'lane_id': self.lane_id, 'max_file_bytes': self.max_file_bytes,
            **({'archive_capture': 'bounded_members_v1'} if self.allow_archives else {}),
            'route_id': refreshed['source_routes']['route_id'], 'batch_id': result['batch_id'],
            'source_files': len(expected), 'source_bytes': sum(row['size_bytes'] for row in expected.values()),
            'observation': 'all_parent_local_sources_measured_before_commit', 'automatic_replay': False})
        return {'route_id': refreshed['source_routes']['route_id'], 'batch_id': result['batch_id'],
                'receipt_id': receipt, 'parent_route_id': self.parent_route_id,
                'source_identity_sha256': values['source_identity_sha256']}


def verify_export_refresh(context, source, *, lane_id, action, mutation_id, effect_id, max_file_bytes,
                          allow_archives=False):
    """Read back the measured Sources publication bound to this export job."""
    from .source_authority import SourceAuthoritySpec, SourceCaptureBudget, freeze_source_authority
    from .source_selection import registered_directory_selection
    route = load_route(context.store, source['route_id'])
    with context.store.lane('receipts').connection(read_only=True) as connection:
        receipt = connection.execute("SELECT body_json FROM receipts WHERE receipt_id=? AND kind='source_refresh_after_export'",
            (source['receipt_id'],)).fetchone()
    if receipt is None or len(receipt[0].encode()) > 65_536:
        return False
    body = json.loads(receipt[0])
    valid = route['batch_id'] == source['batch_id'] and all(body.get(key) == value for key, value in {
        'project_id': context.store.project_id, 'job_id': context.job_id, 'task_id': context.task_id,
        'plan_revision': context.plan_revision, 'action': action, 'lane_id': lane_id, 'max_file_bytes': max_file_bytes,
        'mutation_id': mutation_id, 'effect_id': effect_id, 'route_id': source['route_id'], 'batch_id': source['batch_id'],
        'parent_route_id': source['parent_route_id'], 'source_identity_sha256': source['source_identity_sha256']}.items())
    valid &= body.get('archive_capture') == ('bounded_members_v1' if allow_archives else None)
    capture = SourceCaptureBudget(lambda path: context.source_path(str(path)), lambda: None,
        max_file_bytes=max_file_bytes, allow_archives=allow_archives)
    for row in route['routes']:
        frozen = freeze_source_authority(SourceAuthoritySpec(row['supplied_pointer'], row['ordinal'], row['lane_id'],
            directory_selection=registered_directory_selection(context.store, row['object_id'])), capture=capture)
        valid &= frozen.identity_sha256.lower() == row['identity_sha256'].lower()
    valid &= digest({str(key): value for key, value in sorted(capture.identities.items())}) == source['source_identity_sha256']
    return valid
