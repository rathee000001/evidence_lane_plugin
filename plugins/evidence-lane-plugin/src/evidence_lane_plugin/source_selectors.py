"""Owner-declared sector selectors and verified local-source retirement.

The owning current table retains the last lineage head. A separate immutable
retirement record hides that snapshot from active readers. Refresh binds the
retained head and publishes a later generation; original bytes are never erased.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from pydantic import Field

from .errors import LaneError
from .migrations import apply_migrations, read_compatibility
from .registry import ActionSpec, Contract
from .selector_schema import selector_initialized, selector_migrations
from .source_intake import SourceOperationResult, _source_action_result
from .source_routing import digest
from .storage import bounded_project_read, json_text, now


@dataclass(frozen=True)
class SelectorOwner:
    lane_id: str
    prefix: str
    key_field: str
    migrations: tuple
    read_manifest: Callable
    file_objects: Callable
    scope_paths: Callable

    def validate(self):
        from .lanes import get_lane, owns_schema_object
        lane = get_lane(self.lane_id)
        if (lane.kind != 'sector' or not re.fullmatch(r'[a-z][a-z0-9_]*', self.prefix)
                or not re.fullmatch(r'[a-z][a-z0-9_]*', self.key_field)
                or not self.migrations or not all(callable(value) for value in (
                    self.read_manifest, self.file_objects, self.scope_paths))
                or not any(owns_schema_object(owner, self.prefix + '_current') for owner in lane.schema_owners)):
            raise LaneError('INVALID_SELECTOR_OWNER', 'Bind a retained sector to its actual owning selector and immutable reader.')

    def schema(self):
        return {'lane_id': self.lane_id, 'current_table': self.prefix + '_current',
            'key_field': self.key_field, 'retirement_table': 'selector_retirement',
            'scope': 'owning_local_source_snapshots', 'history_deleted': False}

    def snapshot(self, store, snapshot_id):
        read_compatibility(store.lane(self.lane_id), self.migrations)
        manifest = self.read_manifest(store, snapshot_id)
        if manifest.get('project_id') != store.project_id or manifest.get('lane_id') != self.lane_id:
            raise LaneError('SOURCE_SELECTOR_OWNER', 'The immutable snapshot belongs to another project or sector.')
        objects = self.file_objects(manifest)
        scopes = self.scope_paths(manifest)
        if (not isinstance(objects, dict) or len(objects) > 4096 or not isinstance(scopes, list)
                or not scopes or len(scopes) > 4096 or len(set(scopes)) != len(scopes)
                or any(not isinstance(path, str) or not path for path in [*objects, *scopes])):
            raise LaneError('SOURCE_SELECTOR_LOCAL_REQUIRED', 'Select a bounded snapshot with original local-source paths.')
        files = []
        lane = store.lane(self.lane_id)
        initialized = selector_initialized(lane)
        with lane.connection(read_only=True) as connection:
            for path, identity in sorted(objects.items()):
                if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{64}', identity):
                    raise LaneError('SOURCE_SELECTOR_FILE_INTEGRITY', 'An owning original-source identity is invalid.')
                row = connection.execute('SELECT size_bytes FROM objects WHERE digest=?', (identity,)).fetchone()
                if row is None:
                    raise LaneError('SOURCE_SELECTOR_FILE_INTEGRITY', 'An owning original-source object is missing.')
                files.append({'path': path, 'sha256': identity, 'size_bytes': row[0]})
            key = manifest[self.key_field]
            head = connection.execute(f'SELECT snapshot_id FROM {self.prefix}_current WHERE {self.key_field}=?', (key,)).fetchone()
            retired = connection.execute('SELECT * FROM selector_retirement WHERE snapshot_id=?', (snapshot_id,)).fetchone() if initialized else None
        if retired:
            body = json.loads(lane.read_object(retired['proof_object']))
            if any(body.get(name) != value for name, value in {'project_id': store.project_id,
                    'lane_id': self.lane_id, 'snapshot_id': snapshot_id, 'job_id': retired['job_id'],
                    'plan_revision': retired['plan_revision']}.items()):
                raise LaneError('SOURCE_SELECTOR_RETIREMENT_INTEGRITY', 'The retirement differs from its owning immutable proof.')
        return {'lane_id': self.lane_id, 'snapshot_id': snapshot_id, 'selector_key': key,
            'head_snapshot': head[0] if head else None, 'active': bool(head and head[0] == snapshot_id and not retired),
            'retired': bool(retired), 'retirement': dict(retired) if retired else None,
            'files': files, 'paths': scopes, 'owner': self.schema(), 'generation': manifest['generation'],
            'byte_source': manifest.get('byte_source', 'worktree_files'),
            'source_currentness': 'not_checked_by_metadata_read'}


class SnapshotSelection(Contract):
    lane_id: str = Field(min_length=1, max_length=64)
    snapshot_id: str = Field(pattern=r'^[a-f0-9]{64}$')


def register_file_selector(registry, lane_id, prefix, key_field, migrations, read_manifest,
                           *, source_field='source_object', member_field=None):
    """Bind the owner's exact original-byte fields, including companion files."""
    def files(manifest):
        if member_field:
            return dict(manifest[member_field])
        return {manifest['source_path']: manifest[source_field]} if manifest.get('source_path') else {}
    registry.register_selector(SelectorOwner(lane_id, prefix, key_field, migrations, read_manifest,
        files, lambda manifest: [manifest['source_path']] if manifest.get('source_path') else []))


class SnapshotRetire(SnapshotSelection):
    replacements: list[SnapshotSelection] = Field(default_factory=list, max_length=128)
    replacement_tasks: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=128)
    git_snapshot_id: str | None = Field(default=None, min_length=1, max_length=160)
    max_files: int = Field(default=512, ge=1, le=4096)
    max_total_bytes: int = Field(default=33_554_432, ge=1, le=536_870_912)


def _replacement_tasks(engine, store, request, plan_revision, task_id, *, check=lambda: None):
    """Resolve named earlier tasks only from this exact Plan's verified exits."""
    from .adaptive_delta_exit import read_recorded_exit
    from .jobs import JobQueue
    from .plan_runtime import PlanStore
    if len(set(request.replacement_tasks)) != len(request.replacement_tasks):
        raise LaneError('SOURCE_SELECTOR_REPLACEMENT_SCOPE', 'Select distinct earlier replacement tasks.')
    own_task = PlanStore(store).task(task_id, expected_revision=plan_revision)
    selected, evidence = [], []
    for replacement_id in request.replacement_tasks:
        check()
        task = PlanStore(store).task(replacement_id, expected_revision=plan_revision)
        operation = task.definition.operation
        if task.state != 'completed' or task.position >= own_task.position or operation is None:
            raise LaneError('SOURCE_SELECTOR_REPLACEMENT_TASK', 'A replacement must be an earlier completed bound task in the same Plan.')
        spec = engine.registry.get(operation.action)
        if not spec.source_lanes:
            raise LaneError('SOURCE_SELECTOR_REPLACEMENT_TASK', 'Use an owning source parser task as a replacement.')
        arguments = spec.input_model.model_validate(operation.arguments)
        with store.lane('plan').connection(read_only=True) as connection:
            row = connection.execute('SELECT * FROM delta_runs WHERE task_id=? AND plan_revision=?',
                (replacement_id, plan_revision)).fetchone()
            if row is None or row['contract_digest'] != task.contract_digest or row['result_object'] is None:
                raise LaneError('SOURCE_SELECTOR_REPLACEMENT_TASK', 'The replacement lacks its exact attributed Delta result.')
            job = JobQueue(store).get(row['job_id'], transaction=connection)
            result = json.loads(store.lane('plan').read_object(row['result_object']))
            exit_record = read_recorded_exit(store, connection, row, job, result=result)
        if exit_record is None or result['result']['lane_id'] != arguments.lane_id:
            raise LaneError('SOURCE_SELECTOR_REPLACEMENT_TASK', 'The replacement lacks a verified owning Delta exit.')
        selected.append(SnapshotSelection(lane_id=arguments.lane_id, snapshot_id=result['result']['result']['snapshot_id']))
        evidence.append({'task_id': replacement_id, 'contract_digest': task.contract_digest,
            'job_id': row['job_id'], 'result_object': row['result_object'], 'receipt_id': exit_record.receipt_id})
    return selected, evidence


def _coverage(engine, store, request, path_resolver, tick, *, task_replacements=(), task_evidence=(), observe_git=None):
    tick()
    owner = engine.registry.selector_owner(request.lane_id)
    selected = owner.snapshot(store, request.snapshot_id)
    git = None
    if selected['byte_source'] == 'git_commit_blobs':
        from .source_git_selectors import observe_checkpoint
        git = observe_checkpoint(store, request.git_snapshot_id, path_resolver, request.max_files, observe_git)
        tick()
    elif selected['byte_source'] != 'worktree_files' or request.git_snapshot_id is not None:
        raise LaneError('SOURCE_SELECTOR_BYTE_AUTHORITY', 'Bind retirement to its owning local-file or Git-blob byte authority.')
    if len(selected['files']) > request.max_files:
        raise LaneError('SOURCE_SELECTOR_FILE_BUDGET', 'The complete retired snapshot exceeds the admitted file count.')
    for path in selected['paths']:
        path_resolver(path)
    candidates, refs, inspected = {}, [], len(selected['files'])
    selected_paths = {row['path'] for row in selected['files']}
    seen = {(request.lane_id, request.snapshot_id)}
    for replacement in (*request.replacements, *task_replacements):
        tick()
        key = (replacement.lane_id, replacement.snapshot_id)
        if key in seen:
            raise LaneError('SOURCE_SELECTOR_REPLACEMENT_SCOPE', 'Select distinct replacement snapshots other than the retired snapshot.')
        seen.add(key)
        item = engine.registry.selector_owner(replacement.lane_id).snapshot(store, replacement.snapshot_id)
        inspected += len(item['files'])
        if inspected > 8192:
            raise LaneError('SOURCE_SELECTOR_FILE_BUDGET', 'The selected snapshot inventories exceed the bounded retirement metadata budget.')
        if git is None and item['byte_source'] != 'worktree_files':
            raise LaneError('SOURCE_SELECTOR_GIT_CHECKPOINT_REQUIRED', 'Local original bytes cannot be certified using a Git blob selector.')
        if not item['active']:
            raise LaneError('SOURCE_SELECTOR_REPLACEMENT_STALE', 'Every replacement must remain an active owning snapshot.')
        refs.append(replacement.model_dump(mode='json'))
        # Replacement scopes may contain other files; they are not mutated or
        # admitted as missing by this operation. Only exact overlapping paths count.
        for row in item['files']:
            if row['path'] in selected_paths:
                candidates.setdefault(row['path'], []).append({**row, **replacement.model_dump(mode='json')})
    files, total, used = [], 0, set()
    for row in selected['files']:
        tick()
        path = path_resolver(row['path'])
        total += row['size_bytes']
        if total > request.max_total_bytes:
            raise LaneError('SOURCE_SELECTOR_BYTE_BUDGET', 'The original and replacement bytes exceed the admitted bound.')
        lane = store.lane(request.lane_id)
        if len(lane.read_object(row['sha256'])) != row['size_bytes']:
            raise LaneError('SOURCE_SELECTOR_FILE_INTEGRITY', 'The complete original object differs from its stored size.')
        if git is not None:
            from .source_git_selectors import matches_blob
            oid = git['checkpoint']['blob_ids'].get(row['path'])
            if oid is None:
                files.append({**row, 'disposition': 'absent_from_selected_git_blob_inventory'})
                continue
            matches = []
            for candidate in candidates.get(row['path'], []):
                tick()
                total += candidate['size_bytes']
                if total > request.max_total_bytes:
                    raise LaneError('SOURCE_SELECTOR_BYTE_BUDGET', 'The original and replacement bytes exceed the admitted bound.')
                content = store.lane(candidate['lane_id']).read_object(candidate['sha256'])
                if len(content) != candidate['size_bytes']:
                    raise LaneError('SOURCE_SELECTOR_FILE_INTEGRITY', 'The replacement original object differs from its stored size.')
                if matches_blob(content, oid):
                    matches.append(candidate)
            if len(matches) != 1:
                raise LaneError('SOURCE_SELECTOR_REPLACEMENT_REQUIRED', 'Every retained Git blob needs exactly one active replacement containing that exact committed object.',
                    details={'path': row['path']})
            match = matches[0]
            used.add((match['lane_id'], match['snapshot_id']))
            files.append({**row, 'disposition': 'covered_by_git_replacement', 'git_blob_id': oid, 'replacement': match})
            continue
        try:
            stat = path.stat()
        except FileNotFoundError:
            files.append({**row, 'disposition': 'absent_at_observation'})
            continue
        if not path.is_file():
            raise LaneError('SOURCE_SELECTOR_PATH_CHANGED', 'A former source file became another filesystem object.')
        total += stat.st_size
        if total > request.max_total_bytes:
            raise LaneError('SOURCE_SELECTOR_BYTE_BUDGET', 'The original and replacement bytes exceed the admitted bound.')
        hashed, size = hashlib.sha256(), 0
        with path.open('rb') as stream:
            while chunk := stream.read(65_536):
                tick()
                size += len(chunk)
                if size > stat.st_size:
                    raise LaneError('SOURCE_SELECTOR_SOURCE_CHANGED', 'The source size changed during replacement verification.')
                hashed.update(chunk)
        matches = [item for item in candidates.get(row['path'], [])
            if item['sha256'] == hashed.hexdigest() and item['size_bytes'] == size == stat.st_size]
        if not matches:
            raise LaneError('SOURCE_SELECTOR_REPLACEMENT_REQUIRED', 'A still-present source needs an explicit active replacement preserving its current original bytes.',
                details={'path': row['path']})
        if len({(item['lane_id'], item['snapshot_id']) for item in matches}) != 1:
            raise LaneError('SOURCE_SELECTOR_REPLACEMENT_AMBIGUOUS', 'Choose exactly one current replacement for each still-present source.')
        match = matches[0]
        replacement_lane = store.lane(match['lane_id'])
        if len(replacement_lane.read_object(match['sha256'])) != match['size_bytes']:
            raise LaneError('SOURCE_SELECTOR_FILE_INTEGRITY', 'The replacement original object differs from its stored size.')
        used.add((match['lane_id'], match['snapshot_id']))
        files.append({**row, 'disposition': 'covered_by_replacement', 'replacement': match})
    if used != {(item['lane_id'], item['snapshot_id']) for item in refs}:
        raise LaneError('SOURCE_SELECTOR_REPLACEMENT_SCOPE', 'Every selected replacement must cover at least one still-present retired source.')
    return selected, {'files': files, 'files_omitted': 0, 'observed_bytes': total,
        'replacements': refs, 'replacement_tasks': list(task_evidence), 'git_checkpoint': git,
        'source_bytes_mutated': False, 'historical_bytes_deleted': False}


def register_selector_actions(engine):
    def read(context, request):
        store = engine.directory.open(context.project_id)
        with bounded_project_read(store.root, time.monotonic() + 10):
            body = engine.registry.selector_owner(request.lane_id).snapshot(store, request.snapshot_id)
            return _source_action_result(store, 'source_snapshot_state', body)

    def retire(context, request):
        execution, store = context.execution, context.execution.store
        from .projects import ProjectAccess
        def path(value):
            execution.guard.check()
            selected = execution.guard.path(value)
            ProjectAccess(store).authorize(context.client_id, 'read', path=selected)
            return selected
        replacements, evidence = _replacement_tasks(engine, store, request, context.expected_revision, execution.task_id, check=execution.guard.check)
        def observe_git(arguments):
            result = execution.submit('code_git_checkpoint', arguments).result()
            if result['status'] != 'ok':
                raise LaneError('SOURCE_SELECTOR_GIT_CHECKPOINT_FAILED', 'The exact Git checkpoint worker failed.')
            return result['result']
        selected, coverage = _coverage(engine, store, request, path, execution.guard.check,
            task_replacements=replacements, task_evidence=evidence, observe_git=observe_git)
        if not selected['active']:
            raise LaneError('SOURCE_SELECTOR_NOT_ACTIVE', 'Retire only the exact active selector; inspect historical retirements without replaying them.')
        body = {'schema': 'evidence-lane.source-selector-retirement.v4', 'project_id': store.project_id,
            'lane_id': request.lane_id, 'snapshot_id': request.snapshot_id, 'selector_key': selected['selector_key'],
            'request': request.model_dump(mode='json'), 'request_digest': digest(request.model_dump(mode='json')),
            'job_id': execution.claim.job_id, 'task_id': execution.task_id, 'plan_revision': context.expected_revision,
            'client_id': context.client_id, 'coverage': coverage, 'created_at': now()}
        with execution.lease.coordinated_transaction([request.lane_id, 'receipts']):
            apply_migrations(store.lane(request.lane_id), selector_migrations(request.lane_id), writer=execution.lease)
            again, final = _coverage(engine, store, request, path, execution.guard.check,
                task_replacements=replacements, task_evidence=evidence, observe_git=observe_git)
            if again != selected or final != coverage:
                raise LaneError('SOURCE_SELECTOR_SOURCE_CHANGED', 'The exact retirement observation changed before publication.')
            lane = store.lane(request.lane_id)
            proof = lane.put_object(json_text(body).encode(), limit=2_097_152)
            with lane.transaction() as connection:
                connection.execute('INSERT INTO selector_retirement VALUES(?,?,?,?,?)',
                    (request.snapshot_id, proof, execution.claim.job_id, context.expected_revision, now()))
            receipt = store.append_receipt('source_selector_retired', {'lane_id': request.lane_id,
                'snapshot_id': request.snapshot_id, 'proof_object': proof, 'job_id': execution.claim.job_id,
                'task_id': execution.task_id, 'plan_revision': context.expected_revision})
        return _source_action_result(store, 'source_snapshot_retire', {'lane_id': request.lane_id,
            'snapshot_id': request.snapshot_id, 'proof_object': proof, 'receipt_id': receipt,
            'retired': True, 'historical_bytes_deleted': False, 'source_bytes_mutated': False})

    def verify(context, request, output):
        context.check()
        replacements, evidence = _replacement_tasks(engine, context.store, request, context.plan_revision, context.task_id, check=context.check)
        selected, coverage = _coverage(engine, context.store, request, context.source_path, context.check,
            task_replacements=replacements, task_evidence=evidence)
        row = selected['retirement']
        valid = bool(row and not selected['active'] and selected['head_snapshot'] == request.snapshot_id)
        valid &= len(context.worker_evidence) == (2 if coverage['git_checkpoint'] else 0)
        valid &= all(item['status'] == 'ok' for item in context.worker_evidence)
        if valid:
            proof = json.loads(context.store.lane(request.lane_id).read_object(row['proof_object']))
            valid = all(proof.get(key) == value for key, value in {'request': request.model_dump(mode='json'),
                'request_digest': digest(request.model_dump(mode='json')), 'job_id': context.job_id,
                'task_id': context.task_id, 'plan_revision': context.plan_revision, 'coverage': coverage}.items())
            expected = {'lane_id': request.lane_id, 'snapshot_id': request.snapshot_id,
                'proof_object': row['proof_object'], 'job_id': context.job_id, 'task_id': context.task_id,
                'plan_revision': context.plan_revision}
            with context.store.lane('receipts').connection(read_only=True) as connection:
                receipt = connection.execute("SELECT body_json FROM receipts WHERE receipt_id=? AND kind='source_selector_retired'",
                    (output.result['receipt_id'],)).fetchone()
            valid &= bool(receipt and json.loads(receipt[0]) == expected)
            valid &= output.result == {'lane_id': request.lane_id, 'snapshot_id': request.snapshot_id,
                'proof_object': row['proof_object'], 'receipt_id': output.result['receipt_id'],
                'retired': True, 'historical_bytes_deleted': False, 'source_bytes_mutated': False}
        return [{'check_id': name, 'passed': valid, 'evidence': {'snapshot_id': request.snapshot_id,
            'local_source_dispositions_verified': valid, 'history_deleted': False}} for name in context.requested_checks]

    engine.registry.register(ActionSpec('source_snapshot_state',
        'Inspect an owning local-source snapshot, retained lineage head and retirement without refreshing source bytes.',
        SnapshotSelection, SourceOperationResult, read, profile='sources', workflow='refresh-project-evidence',
        queryable_in_delta=True, cross_project_read=True, studio_read=True))
    from .lanes import SECTOR_LANE_IDS, lane_family
    from .tool_routes import ToolRoute
    engine.registry.register(ActionSpec('source_snapshot_retire',
        'Retire an exact active local-source selector after verifying every file is absent or has an explicit current replacement; preserve historical bytes.',
        SnapshotRetire, SourceOperationResult, retire, permission='write', mutates=True, requires_delta=True,
        profile='sources', workflow='refresh-project-evidence', required_tools=('Python',),
        verification_checks=('source_selector_retirement_verified',), verifier=verify,
        worker_operations=('code_git_checkpoint',), tool_routes=(
            ToolRoute('source_selector.local_files', retire, ('Python',),
                applicable=lambda _context, request: lane_family(request.lane_id) in SECTOR_LANE_IDS and request.lane_id != 'github_code',
                worker_operations=()),
            ToolRoute('source_selector.git_checkpoint', retire, ('Python', 'Git'),
                argument_values=(('lane_id', ('github_code',)),), worker_operations=('code_git_checkpoint',)))))
