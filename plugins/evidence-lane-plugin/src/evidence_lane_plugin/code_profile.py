"""Executable local/GitHub Code lanes over immutable file and parser snapshots.

Registered Delta operations own indexing and exact file replacements. Reads
select immutable snapshots. Import impact is explicitly static and bounded;
historical Git provenance stays in Sources, referenced by exact digest.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import Field, JsonValue, model_validator

from .code_profile_schema import code_migrations
from .errors import LaneError
from .hashing import canonical_json_bytes
from .migrations import apply_migrations, read_compatibility
from .projects import ProjectAccess
from .registry import ActionSpec, Contract, SearchRoute, SourceMaterialization
from .selector_schema import active_selector_sql, is_retired
from .source_policy import path_exclusion_reason
from .storage import bounded_project_read, json_text, now, project_snapshot, reject_links
from .tool_routes import ToolRoute

DIGEST = r'^[0-9a-f]{64}$'
CODE_MANIFEST_BYTES = 2_097_152
CodeLane = Literal['local_code', 'github_code']
FACT_TABLES = {kind: 'code_' + kind for kind in (
    'symbol', 'import', 'call', 'route', 'dependency', 'parser_receipt', 'parser_diagnostic')}


def sha256_bytes(content):
    # v4 addressed lane files use lowercase hashes; the admitted legacy helper
    # deliberately retains uppercase formatting for its historical receipts.
    return hashlib.sha256(content).hexdigest()


class CodeSelection(Contract):
    lane_id: CodeLane = 'local_code'


class _CodeIndexFields(CodeSelection):
    paths: list[str] = Field(min_length=1, max_length=32)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)
    max_files: int = Field(default=64, ge=1, le=512)
    max_file_bytes: int = Field(default=262_144, ge=1, le=1_048_576)
    max_total_bytes: int = Field(default=4_194_304, ge=1, le=33_554_432)
    git_snapshot_id: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode='after')
    def distinct_paths(self):
        if len(set(self.paths)) != len(self.paths):
            raise ValueError('Select distinct source paths')
        if self.lane_id == 'github_code' and self.git_snapshot_id is None:
            raise ValueError('GitHub Code requires an exact registered Sources Git snapshot')
        return self


class CodeIndex(_CodeIndexFields):
    lane_id: Literal['local_code'] = 'local_code'


class CodeSnapshot(CodeSelection):
    snapshot_id: str = Field(pattern=DIGEST)


class CodeGitIndex(_CodeIndexFields):
    lane_id: Literal['github_code'] = 'github_code'
    git_snapshot_id: str = Field(min_length=1, max_length=160)


class CodeQuery(CodeSnapshot):
    collection: Literal['text', 'files', 'symbols', 'imports', 'calls', 'routes', 'dependencies', 'receipts', 'diagnostics', 'changes'] = 'text'
    match_mode: Literal['all', 'any'] = 'all'
    query: str | None = Field(default=None, min_length=1, max_length=500)
    path_prefix: str | None = Field(default=None, min_length=1, max_length=1000)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    limit: int = Field(default=20, ge=1, le=100)
    max_bytes: int = Field(default=65_536, ge=2048, le=262_144)


class CodeImpact(CodeSnapshot):
    paths: list[str] = Field(min_length=1, max_length=32)
    direction: Literal['dependents', 'dependencies'] = 'dependents'
    max_depth: int = Field(default=2, ge=1, le=8)
    max_files: int = Field(default=100, ge=1, le=200)
    max_edges: int = Field(default=200, ge=0, le=500)

    @model_validator(mode='after')
    def bounded_seeds(self):
        paths = [_relative(path) for path in self.paths]
        if len(set(paths)) != len(paths) or len(paths) > self.max_files:
            raise ValueError('Select distinct impact seeds within max_files')
        return self


class CodeRead(CodeSnapshot):
    filename: str = Field(min_length=1, max_length=1000)
    start_line: int = Field(default=1, ge=1)
    line_count: int = Field(default=80, ge=1, le=200)
    max_bytes: int = Field(default=65_536, ge=1024, le=262_144)


class CodeApply(CodeSnapshot):
    lane_id: Literal['local_code'] = 'local_code'
    filename: str = Field(min_length=1, max_length=1000)
    expected_sha256: str = Field(pattern=DIGEST)
    replacement_utf8: str = Field(max_length=524_288)


class CodeResult(Contract):
    project_id: str
    lane_id: CodeLane
    operation: str
    result: dict[str, JsonValue]


def _result(store, lane_id, operation, body, *, limit=2_097_152):
    value = CodeResult(project_id=store.project_id, lane_id=lane_id, operation=operation, result=body)
    if len(json_text(value.model_dump(mode='json')).encode()) > limit:
        raise LaneError('CODE_OUTPUT_BUDGET', 'Select a smaller Code query or indexing batch.')
    return value


def parser_contract(syntax):
    from . import code_parsers, code_toolchain, code_workers, dependency_detection, source_policy
    modules = [code_parsers, code_workers, dependency_detection, source_policy] + ([code_toolchain] if syntax else [])
    packages = {}
    for name in ('tree-sitter', 'tree-sitter-language-pack') if syntax else ():
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    assets = None
    if syntax:
        from .shared_tool_assets import resolve_shared_asset
        _, asset = resolve_shared_asset('parser_grammars')
        assets = {key: asset[key] for key in ('asset_id', 'version', 'languages', 'files_sha256')}
    return sha256_bytes(canonical_json_bytes({'syntax': syntax, 'version': 4,
        'runtime': {'implementation': sys.implementation.name, 'version': list(sys.version_info), 'packages': packages},
        'assets': assets,
        'implementations': {Path(module.__file__).name: sha256_bytes(Path(module.__file__).read_bytes()) for module in modules}}))


def _relative(value):
    path = PurePosixPath(value.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or ':' in str(path) or '\x00' in str(path) or not str(path):
        raise LaneError('CODE_PATH_INVALID', 'Select a project-relative path without traversal or alternate streams.')
    return path.as_posix()


def _read_bytes(path, limit):
    reject_links(path, Path(path.anchor))
    with path.open('rb') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise LaneError('CODE_FILE_BYTE_BUDGET', 'The selected file exceeds its explicit byte budget.')
    return value


def _snapshot(lane, snapshot_id):
    if lane is None:
        raise LaneError('CODE_SNAPSHOT_MISSING', 'Index the selected Code sources first.')
    read_compatibility(lane, code_migrations(lane.lane_id))
    with lane.connection(read_only=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='code_snapshot'").fetchone():
            raise LaneError('CODE_SNAPSHOT_MISSING', 'Index the selected Code sources first.')
        row = connection.execute('SELECT * FROM code_snapshot WHERE snapshot_id=?', (snapshot_id,)).fetchone()
        size = connection.execute('SELECT size_bytes FROM objects WHERE digest=?', (row['manifest_object'],)).fetchone() if row else None
    if row is None:
        raise LaneError('CODE_SNAPSHOT_MISSING', 'Select an exact snapshot from this Code lane.')
    if size is None or not 0 < size[0] <= CODE_MANIFEST_BYTES:
        raise LaneError('CODE_MANIFEST_BUDGET', 'The selected Code manifest exceeds its declared read budget.')
    with lane.object_path(row['manifest_object']).open('rb') as stream:
        raw = stream.read(CODE_MANIFEST_BYTES + 1)
    if len(raw) > CODE_MANIFEST_BYTES:
        raise LaneError('CODE_MANIFEST_BUDGET', 'The selected Code manifest exceeds its read budget.')
    if len(raw) != size[0] or sha256_bytes(raw) != row['manifest_object']:
        raise LaneError('CODE_SNAPSHOT_INTEGRITY', 'The Code manifest bytes differ from their addressed identity.')
    manifest = json.loads(raw)
    if (sha256_bytes(canonical_json_bytes(manifest)) != snapshot_id
            or manifest['project_id'] != lane.project_id or manifest['lane_id'] != lane.lane_id
            or any(manifest[key] != row[key] for key in ('scope_id', 'generation', 'previous_snapshot', 'parser_contract'))):
        raise LaneError('CODE_SNAPSHOT_INTEGRITY', 'The Code manifest differs from its snapshot identity.')
    return dict(row), manifest


def code_lane(store, lane_id):
    try:
        return store.lane(lane_id)
    except LaneError as error:
        if error.code != 'LANE_NOT_INITIALIZED':
            raise
        return None


def _enumerate(context, request, *, missing_roots=(), source_route=None):
    execution, source = context.execution, context.execution.store.source_root
    source_route = source_route or execution.guard.source_route
    roots = sorted({execution.guard.path(value) for value in request.paths})
    files, exclusions, visited = {}, [], 0
    selected_inputs = source_route.selected_input_paths() if source_route else None
    stack = list(reversed(roots if selected_inputs is None else selected_inputs))
    while stack:
        path = stack.pop()
        execution.guard.check()
        execution.guard.path(str(path))
        ProjectAccess(execution.store).authorize(context.client_id, 'read', path=path)
        relative = path.relative_to(source).as_posix()
        reason = path_exclusion_reason(relative) if relative != '.' else None
        if reason:
            exclusions.append({'path': relative, 'reason': reason})
            continue
        reject_links(path, source)
        visited += 1
        if visited > request.max_files * 40 + 100:
            raise LaneError('CODE_WALK_BUDGET', 'Select fewer source directories.')
        if path.is_dir():
            # Bound each enumeration before materializing a directory listing.
            children = []
            with os.scandir(path) as iterator:
                for item in iterator:
                    if len(children) >= request.max_files * 40 + 100:
                        raise LaneError('CODE_WALK_BUDGET', 'Select a smaller source directory.')
                    children.append(Path(item.path))
            stack.extend(sorted(children, reverse=True))
        elif path.is_file():
            if source_route is not None and not source_route.allows(path):
                exclusions.append({'path': relative, 'reason': 'SOURCE_ROUTED_TO_OTHER_LANE_OR_OCCURRENCE'})
                continue
            files[relative] = path
            if len(files) > request.max_files:
                raise LaneError('CODE_FILE_COUNT_BUDGET', 'Increase the explicit file budget or select a smaller source scope.')
        elif relative not in missing_roots or path.exists():
            raise LaneError('CODE_SOURCE_MISSING', 'A selected source path does not exist or is not a regular file.')
    return sorted(path.relative_to(source).as_posix() for path in roots), dict(sorted(files.items())), exclusions


def require_code_calls(execution, remaining):
    """Retain room for the verifier before parsing or preparing an effect."""
    execution.guard.check()
    if execution.guard.calls + remaining > execution.guard.task.budget.max_tool_calls:
        raise LaneError('DELTA_TOOL_BUDGET', 'The selected Code batch must leave room for its required verification.')


def _git_reference(store, snapshot_id):
    if snapshot_id is None:
        return None
    from .source_authority import SOURCES_MIGRATIONS
    read_compatibility(store, SOURCES_MIGRATIONS)
    with store.lane('sources').connection(read_only=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='source_git_snapshot'").fetchone():
            raise LaneError('CODE_GIT_REFERENCE_MISSING', 'Register and inspect the exact Git history in Sources first.')
        row = connection.execute('SELECT * FROM source_git_snapshot WHERE snapshot_id=?', (snapshot_id,)).fetchone()
        source = connection.execute('SELECT resolved_pointer FROM source_object WHERE object_id=?',
            (row['object_id'],)).fetchone() if row else None
    if row is None:
        raise LaneError('CODE_GIT_REFERENCE_MISSING', 'Select a registered Sources Git snapshot.')
    body = dict(row)
    if source is None or Path(source['resolved_pointer']).resolve() != store.source_root:
        raise LaneError('CODE_GIT_PROJECT_MISMATCH', 'The selected Git history belongs to another source repository.')
    # Keep the complete measured identity as an attributed Sources reference;
    # the working copy is indexed independently, including its dirty bytes.
    reference = {'source_lane': 'sources', 'snapshot_id': snapshot_id, 'batch_id': body['batch_id'],
                 'source_record_sha256': sha256_bytes(canonical_json_bytes(body)),
                 'head_commit_sha': body['head_commit_sha'], 'head_tree_sha': body['head_tree_sha'],
                 'registered_worktree_clean': bool(body['worktree_clean'])}
    return reference


def resolve_import(from_path, imported, available):
    """Resolve a unique literal path only. Runtime aliases remain unresolved."""
    module = imported.get('module', '').strip()
    candidates = set()
    parent = PurePosixPath(from_path).parent
    if imported.get('parser') == 'python-ast':
        level = len(module) - len(module.lstrip('.'))
        if level:
            parts = list(parent.parts)
            if level > len(parts) + 1:
                return None, 'unresolved', module
            base = PurePosixPath(*parts[:len(parts) - level + 1])
        else:
            base = PurePosixPath('.')
        target = base / module.lstrip('.').replace('.', '/')
        stems = [target]
        if imported.get('imported_name') not in {None, '*'}:
            stems.insert(0, target / imported['imported_name'])
        for stem in stems:
            candidates.update({str(stem) + '.py', (stem / '__init__.py').as_posix()})
    elif module.startswith('.') and '\n' not in module:
        joined = os.path.normpath(str(parent / module)).replace('\\', '/')
        if joined.startswith('../') or joined == '..':
            return None, 'unresolved', module
        candidates.add(joined)
        candidates.update(joined + suffix for suffix in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'))
        candidates.update(joined + '/index' + suffix for suffix in ('.js', '.jsx', '.ts', '.tsx'))
    matches = sorted(candidates & available)
    return (matches[0], 'unique_static_path', module) if len(matches) == 1 else (
        None, 'ambiguous_static_path' if matches else 'unresolved', module)


def index_code(context, request, *, syntax=False, git_checkpoint=False, source_route=None, source_observation_route_id=None,
               prepared_files=None):
    execution = context.execution
    if execution is None or execution.guard is None:
        raise LaneError('CODE_DELTA_REQUIRED', 'Index Code through an admitted Plan task.')
    store, lane = execution.store, code_lane(execution.store, request.lane_id)
    source_route = source_route or execution.guard.source_route
    if source_route:
        source_route.check()
    if not git_checkpoint and (request.lane_id != 'local_code' or request.git_snapshot_id is not None):
        raise LaneError('CODE_GIT_OPERATION_REQUIRED', 'Use the registered Git checkpoint indexing operation for GitHub Code.')
    contract = parser_contract(syntax)
    missing_roots = ()
    if request.expected_snapshot:
        _, prior = _snapshot(lane, request.expected_snapshot)
        if prior.get('source_route') and source_route is None:
            raise LaneError('SOURCE_ROUTE_SELECTION_REQUIRED', 'Refresh this routed Code scope with its exact current source-route selection.')
        source_observation_route_id = (source_observation_route_id or
            (source_route.selection.route_id if source_route else None) or prior.get('source_observation_route_id'))
        selected_roots = sorted({execution.guard.path(value).relative_to(store.source_root).as_posix() for value in request.paths})
        if prior['paths'] == selected_roots:
            missing_roots = selected_roots
    roots, files, exclusions = _enumerate(context, request, missing_roots=missing_roots, source_route=source_route)
    require_code_calls(execution, (2 if git_checkpoint else 0) + 1)
    scope_id = sha256_bytes(canonical_json_bytes([store.project_id, request.lane_id, roots]))
    reference = _git_reference(store, request.git_snapshot_id)
    def check_git():
        if not git_checkpoint:
            return None
        response = execution.submit('code_git_checkpoint', {'repository': str(store.source_root),
            'max_files': request.max_files}).result()
        if response['status'] != 'ok':
            raise LaneError('CODE_GIT_CHECKPOINT_FAILED', 'The exact local Git checkpoint could not be measured.')
        observed = response['result']
        if (not reference['registered_worktree_clean'] or not observed['clean']
                or observed['head_commit_sha'] != reference['head_commit_sha']
                or observed['head_tree_sha'] != reference['head_tree_sha']):
            raise LaneError('CODE_GIT_CHECKPOINT_CHANGED', 'GitHub Code requires the selected clean Sources Git checkpoint.')
        return observed
    checkpoint = check_git()
    if checkpoint:
        ignored = set(files) - set(checkpoint['tracked_paths'])
        exclusions.extend({'path': path, 'reason': 'NOT_IN_SELECTED_GIT_CHECKPOINT'} for path in sorted(ignored))
        files = {path: value for path, value in files.items() if path not in ignored}
    previous, generation, old_files = None, 1, {}
    old, prior_manifest = None, None
    read_compatibility(store, code_migrations(request.lane_id))
    if lane is not None:
        with lane.connection(read_only=True) as connection:
            if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='code_current'").fetchone():
                row = connection.execute('SELECT snapshot_id FROM code_current WHERE scope_id=?', (scope_id,)).fetchone()
                previous = row[0] if row else None
    if previous != request.expected_snapshot:
        raise LaneError('CODE_SNAPSHOT_CHANGED', 'Bind refresh to the exact current snapshot for this source scope.')
    if previous:
        old, prior_manifest = _snapshot(lane, previous)
        generation = old['generation'] + 1
        old_files = {row['path']: row['sha256'] for row in prior_manifest['files']}
    # Capture the entire selected working scope, including files later excluded
    # by content policy. Reuse never substitutes stored bytes for a live check.
    identities, policy_excluded, total = {}, {}, 0
    from .source_policy import content_exclusion_reason
    for relative, path in files.items():
        size = path.stat().st_size
        if size > request.max_file_bytes or total + size > request.max_total_bytes:
            raise LaneError('CODE_TOTAL_BYTE_BUDGET', 'Select a smaller source batch or increase its byte budget.')
        raw = _read_bytes(path, request.max_file_bytes)
        total += len(raw)
        if total > request.max_total_bytes:
            raise LaneError('CODE_TOTAL_BYTE_BUDGET', 'The observed source bytes exceed the selected batch budget.')
        identities[relative] = {'sha256': sha256_bytes(raw), 'size_bytes': len(raw)}
        if not checkpoint:
            reason = content_exclusion_reason(raw)
            if reason:
                policy_excluded[relative] = reason
        execution.guard.check()
    reusable = {}
    if not checkpoint and prior_manifest and old['parser_contract'] == contract:
        candidates = {row['path']: row for row in prior_manifest['files']
            if row['path'] not in policy_excluded
            and identities.get(row['path']) == {key: row[key] for key in ('sha256', 'size_bytes')}}
        if candidates:
            if not verify_snapshot_records(lane, old, prior_manifest):
                raise LaneError('CODE_REUSE_INTEGRITY', 'The previous snapshot failed verification before unchanged-file reuse.')
            with lane.connection(read_only=True) as connection:
                for relative, row in candidates.items():
                    version = connection.execute('SELECT v.* FROM code_snapshot_file sf JOIN code_file_version v USING(version_id) '
                        'WHERE sf.snapshot_id=? AND sf.path=?', (previous, relative)).fetchone()
                    reusable[relative] = {**row, 'reused_version_id': version['version_id'],
                        'facts': json.loads(lane.read_object(row['facts_digest']))}
    prepared_files = prepared_files or {}
    if not set(prepared_files) <= set(files) - set(reusable) - set(policy_excluded) or (prepared_files and git_checkpoint):
        raise LaneError('CODE_WORKER_BINDING', 'Prepared parser results must belong to changed local source files.')
    require_code_calls(execution, len(files) - len(reusable) - len(policy_excluded) - len(prepared_files) + (1 if git_checkpoint else 0) + 1)
    parsed, worker_calls = [], 0
    for relative, path in files.items():
        if relative in policy_excluded:
            exclusions.append({'path': relative, 'reason': policy_excluded[relative]})
            continue
        if relative in reusable:
            parsed.append(reusable[relative])
            continue
        arguments = {'relative_path': relative, 'max_file_bytes': request.max_file_bytes, 'syntax': syntax}
        if relative in prepared_files:
            response = prepared_files[relative]
        elif checkpoint:
            response = execution.submit('code_parse_git_blob', {**arguments, 'repository': str(store.source_root),
                'blob_id': checkpoint['blob_ids'][relative]}).result()
        else:
            response = execution.submit('code_parse_file', {**arguments, 'filename': str(path)}).result()
        worker_calls += 1
        if response['status'] != 'ok':
            raise LaneError(response.get('code', 'CODE_PARSER_FAILED'), 'A selected Code parser worker failed.')
        # Keep the completed worker result immutable for receipt hashing and
        # cumulative output accounting; decoding bytes uses a detached copy.
        row = json.loads(json_text(response['result']))
        if row['path'] != relative:
            raise LaneError('CODE_WORKER_BINDING', 'The Code worker returned another source path.')
        if row.get('excluded'):
            exclusions.append({'path': relative, 'reason': row['excluded']})
            continue
        raw = base64.b64decode(row.pop('content_base64'), validate=True)
        if sha256_bytes(raw) != row['sha256'] or len(raw) != row['size_bytes']:
            raise LaneError('CODE_WORKER_BINDING', 'The Code worker bytes failed hash validation.')
        if not checkpoint and identities[relative] != {'sha256': row['sha256'], 'size_bytes': len(raw)}:
            raise LaneError('CODE_SOURCE_CHANGED', 'A selected source file changed after the refresh identity check.')
        row['content'] = raw
        parsed.append(row)
        if sum(item['size_bytes'] for item in parsed) > request.max_total_bytes:
            raise LaneError('CODE_TOTAL_BYTE_BUDGET', 'The parsed bytes exceed the selected batch budget.')
        execution.guard.observe(execution)
    # Detect both changed bytes and added/deleted files before committing an
    # immutable snapshot. The filesystem and SQLite are distinct authorities.
    _, final_files, _ = _enumerate(context, request, missing_roots=missing_roots, source_route=source_route)
    if checkpoint:
        final_files = {path: value for path, value in final_files.items() if path in checkpoint['tracked_paths']}
    if set(final_files) != set(files):
        raise LaneError('CODE_SOURCE_CHANGED', 'The selected source membership changed while parsing.')
    for relative, expected in identities.items():
        execution.guard.check()
        if sha256_bytes(_read_bytes(files[relative], request.max_file_bytes)) != expected['sha256']:
            raise LaneError('CODE_SOURCE_CHANGED', 'A selected source file changed while parsing.')
    if _git_reference(store, request.git_snapshot_id) != reference:
        raise LaneError('CODE_GIT_REFERENCE_CHANGED', 'The selected Sources Git reference changed.')
    if check_git() != checkpoint:
        raise LaneError('CODE_GIT_CHECKPOINT_CHANGED', 'The selected Git checkpoint changed during indexing.')
    if parser_contract(syntax) != contract:
        raise LaneError('CODE_PARSER_CONTRACT_CHANGED', 'The parser implementation, runtime or grammar assets changed during indexing.')
    if source_route:
        source_route.check()
    repo_id = sha256_bytes(canonical_json_bytes([store.project_id, str(store.source_root)]))
    stamp = now()
    manifest = {'schema': 'evidence-lane.code-snapshot.v4', 'project_id': store.project_id,
        'lane_id': request.lane_id, 'source_root': str(store.source_root), 'scope_id': scope_id,
        'generation': generation, 'previous_snapshot': previous, 'paths': roots, 'parser_contract': contract,
        'created_at': stamp, 'git_reference': reference,
        'capture_limits': {key: getattr(request, key) for key in ('max_files', 'max_file_bytes', 'max_total_bytes')},
        'source_route': source_route.selection.model_dump(mode='json') if source_route else None,
        'source_observation_route_id': source_observation_route_id or (source_route.selection.route_id if source_route else None),
        'files': [{**{key: row[key] for key in ('path', 'sha256', 'size_bytes', 'parser_state', 'facts_digest')},
                   'git_blob_id': row.get('git_blob_id')}
                  for row in parsed], 'exclusions': exclusions,
        'syntax_requested': syntax, 'byte_source': 'git_commit_blobs' if checkpoint else 'worktree_files',
        'working_copy_matches_git_commit': 'clean_git_status_observed_no_byte_equality_inferred' if checkpoint else 'not_inferred',
        'source_unchanged_check': 'observed_before_commit_not_filesystem_snapshot',
        'refresh': {'policy': 'same_scope_hash_and_parser_contract_v1', 'reused_files': len(reusable),
            'parser_worker_calls': worker_calls, 'enumerated_files': len(identities),
            'policy_excluded_files': len(policy_excluded),
            'source_identity_sha256': sha256_bytes(canonical_json_bytes(identities)),
            'parser_contract_changed': bool(old and old['parser_contract'] != contract)}}
    snapshot_id = sha256_bytes(canonical_json_bytes(manifest))
    current_hashes = {row['path']: row['sha256'] for row in parsed}
    changes = [{'path': path, 'before_sha256': old_files.get(path), 'after_sha256': current_hashes.get(path),
        'change_kind': 'added' if path not in old_files else 'deleted' if path not in current_hashes else
                       'unchanged' if old_files[path] == current_hashes[path] else 'modified'}
        for path in sorted(old_files.keys() | current_hashes.keys())]
    # Re-observe unchanged sources without replacing the current Code index or
    # invalidating its natural views. A separate Receipts event records this run.
    unchanged_index = bool(prior_manifest and not is_retired(lane, previous) and all(prior_manifest.get(key) == manifest[key]
        for key in ('paths', 'parser_contract', 'git_reference', 'files', 'exclusions', 'syntax_requested', 'byte_source',
                    'capture_limits', 'source_route', 'source_observation_route_id')))
    refresh = {**manifest['refresh'], 'publication': 'reused_current_snapshot' if unchanged_index else 'new_snapshot'}
    execution._before_more_work()
    if unchanged_index:
        with execution.lease.transaction('receipts'):
            with lane.connection(read_only=True) as connection:
                live = connection.execute('SELECT snapshot_id FROM code_current WHERE scope_id=?', (scope_id,)).fetchone()
            if live is None or live[0] != previous:
                raise LaneError('CODE_SNAPSHOT_CHANGED', 'The Code scope changed before refresh observation.')
            refresh_receipt = _record_refresh(store, execution, request.lane_id, previous, previous, scope_id, refresh)
        return _result(store, request.lane_id, execution.guard.spec.name,
            {'snapshot_id': previous, 'scope_id': scope_id, 'generation': old['generation'],
             'files': len(parsed), 'excluded': len(exclusions), 'bytes': sum(row['size_bytes'] for row in parsed),
             'changes': {kind: sum(row['change_kind'] == kind for row in changes) for kind in ('added', 'modified', 'deleted', 'unchanged')},
             'parser_states': sorted({row['parser_state'] for row in parsed}), 'git_reference': reference,
             'graph_generated': False, 'source_bytes_mutated': False, 'refresh': refresh, 'refresh_receipt_id': refresh_receipt})
    with execution.lease.coordinated_transaction([request.lane_id, 'receipts']):
        lane = store.lane(request.lane_id)
        apply_migrations(lane, code_migrations(request.lane_id), writer=execution.lease)
        with lane.transaction() as connection:
            live = connection.execute('SELECT snapshot_id FROM code_current WHERE scope_id=?', (scope_id,)).fetchone()
            if (live[0] if live else None) != previous:
                raise LaneError('CODE_SNAPSHOT_CHANGED', 'The Code scope changed before publication.')
            connection.execute('INSERT OR IGNORE INTO code_repo VALUES(?,?,?,?)', (repo_id, store.project_id, str(store.source_root), stamp))
            manifest_object = lane.put_object(canonical_json_bytes(manifest))
            connection.execute('INSERT INTO code_snapshot VALUES(?,?,?,?,?,?,?,?)',
                (snapshot_id, repo_id, scope_id, generation, previous, manifest_object, contract, stamp))
            for row in parsed:
                if 'reused_version_id' in row:
                    connection.execute('INSERT INTO code_snapshot_file VALUES(?,?,?)',
                        (snapshot_id, row['path'], row['reused_version_id']))
                else:
                    _insert_file(lane, connection, row, repo_id, snapshot_id, contract)
            available = set(current_hashes)
            for row in parsed:
                for imported in row['facts']['import']:
                    target, resolution, module = resolve_import(row['path'], imported, available)
                    edge = (row['path'], module, target, imported.get('line_number', 1), resolution)
                    connection.execute('INSERT OR IGNORE INTO code_import_edge VALUES(?,?,?,?,?,?,?)',
                        (snapshot_id, *edge, json_text(edge)))
            for change in changes:
                connection.execute('INSERT INTO code_change VALUES(?,?,?,?,?)',
                    (snapshot_id, change['path'], change['change_kind'], change['before_sha256'], change['after_sha256']))
            if reference:
                connection.execute('INSERT INTO code_git_reference VALUES(?,?,?,?,?)', (snapshot_id,
                    reference['batch_id'], reference['snapshot_id'], sha256_bytes(canonical_json_bytes(reference)), json_text(reference)))
            connection.execute('INSERT INTO code_current VALUES(?,?) ON CONFLICT(scope_id) DO UPDATE SET snapshot_id=excluded.snapshot_id',
                               (scope_id, snapshot_id))
            store.append_receipt('code_snapshot', {'lane_id': request.lane_id, 'snapshot_id': snapshot_id,
                'scope_id': scope_id, 'job_id': execution.claim.job_id, 'task_id': execution.task_id,
                'plan_revision': execution.plan_revision, 'source_bytes_mutated': False, 'refresh': manifest['refresh']})
            refresh_receipt = _record_refresh(store, execution, request.lane_id, snapshot_id, previous, scope_id, refresh)
    return _result(store, request.lane_id, execution.guard.spec.name,
        {'snapshot_id': snapshot_id, 'scope_id': scope_id, 'generation': generation,
         'files': len(parsed), 'excluded': len(exclusions), 'bytes': sum(row['size_bytes'] for row in parsed),
         'changes': {kind: sum(row['change_kind'] == kind for row in changes) for kind in ('added', 'modified', 'deleted', 'unchanged')},
         'parser_states': sorted({row['parser_state'] for row in parsed}), 'git_reference': reference,
         'graph_generated': False, 'source_bytes_mutated': False, 'refresh': refresh, 'refresh_receipt_id': refresh_receipt})


def _record_refresh(store, execution, lane_id, snapshot_id, previous, scope_id, refresh):
    return store.append_receipt('code_refresh_observed', {'project_id': store.project_id, 'lane_id': lane_id,
        'snapshot_id': snapshot_id, 'previous_snapshot': previous, 'scope_id': scope_id,
        'job_id': execution.claim.job_id, 'task_id': execution.task_id, 'plan_revision': execution.plan_revision,
        'source_bytes_mutated': False, 'refresh': refresh})


def _insert_file(lane, connection, row, repo_id, snapshot_id, contract):
    file_id = sha256_bytes(canonical_json_bytes([repo_id, row['path']]))
    version_id = sha256_bytes(canonical_json_bytes([file_id, row['sha256'], contract]))
    content = lane.put_object(row['content'])
    if lane.put_object(canonical_json_bytes(row['facts'])) != row['facts_digest']:
        raise LaneError('CODE_FACT_INTEGRITY', 'The parser facts differ from their exact result digest.')
    connection.execute('INSERT OR IGNORE INTO code_file VALUES(?,?,?)', (file_id, repo_id, row['path']))
    exists = connection.execute('SELECT 1 FROM code_file_version WHERE version_id=?', (version_id,)).fetchone()
    if not exists:
        connection.execute('INSERT INTO code_file_version VALUES(?,?,?,?,?,?,?,?,?)', (version_id, file_id, content,
            row['size_bytes'], row['encoding'], row['language'], contract, row['parser_state'], row['facts_digest']))
        for chunk in row['chunks']:
            identity = sha256_bytes(canonical_json_bytes([version_id, chunk['ordinal']]))
            obj = lane.put_object(chunk['text'].encode('utf-8'))
            connection.execute('INSERT INTO code_chunk VALUES(?,?,?,?,?,?)',
                (identity, version_id, chunk['ordinal'], chunk['start_line'], chunk['end_line'], obj))
            connection.execute('INSERT INTO code_chunk_fts(chunk_id,text_content) VALUES(?,?)', (identity, chunk['text']))
        for kind, table in FACT_TABLES.items():
            for ordinal, fact in enumerate(row['facts'][kind]):
                identity = sha256_bytes(canonical_json_bytes([version_id, kind, ordinal]))
                name = fact_name(fact)
                line = fact.get('start_line', fact.get('line_number', 1)) or 1
                connection.execute('INSERT INTO ' + table + ' VALUES(?,?,?,?,?,?)',
                    (identity, version_id, ordinal, name, line, json_text(fact)))
    connection.execute('INSERT INTO code_snapshot_file VALUES(?,?,?)', (snapshot_id, row['path'], version_id))


def fact_name(fact):
    return str(fact.get('qualified_name') or fact.get('name') or fact.get('module') or
               fact.get('callee') or fact.get('path_pattern') or fact.get('parser') or '')


def snapshot_relations(lane, manifest):
    """Rebuild static edges from hash-checked facts, with a bounded read set."""
    available = {item['path'] for item in manifest['files']}
    edges, read_bytes = set(), 0
    for source in manifest['files']:
        content = lane.read_object(source['facts_digest'])
        read_bytes += len(content)
        if read_bytes > 16_777_216:
            raise LaneError('CODE_QUERY_READ_BUDGET', 'Select a smaller Code source scope for relationship queries.')
        for fact in json.loads(content)['import']:
            target, resolution, module = resolve_import(source['path'], fact, available)
            edges.add((source['path'], module, target, fact.get('line_number', 1), resolution))
            if len(edges) > 32768:
                raise LaneError('CODE_QUERY_READ_BUDGET', 'Select a smaller Code relationship scope.')
    return edges


def verify_code_index(context, request, output):
    lane = context.store.lane(request.lane_id)
    row, manifest = _snapshot(lane, output.result['snapshot_id'])
    stored = all(sha256_bytes(lane.read_object(item['sha256'])) == item['sha256'] for item in manifest['files'])
    if isinstance(request, CodeGitIndex):
        from .code_workers import git_checkpoint
        observed = git_checkpoint({'repository': str(context.source_path('.')), 'max_files': request.max_files})
        live = (observed['clean'] and observed['head_commit_sha'] == manifest['git_reference']['head_commit_sha']
                and observed['head_tree_sha'] == manifest['git_reference']['head_tree_sha'])
        for item in manifest['files']:
            content = lane.read_object(item['sha256'])
            oid = item['git_blob_id']
            algorithm = 'sha1' if len(oid) == 40 else 'sha256'
            live &= (hashlib.new(algorithm, b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest() == oid
                     and observed['blob_ids'].get(item['path']) == oid)
    else:
        live = all(sha256_bytes(_read_bytes(context.source_path(item['path']), request.max_file_bytes)) == item['sha256']
                   for item in manifest['files'])
    stored &= verify_snapshot_records(lane, row, manifest)
    refresh = output.result.get('refresh')
    if refresh is not None:
        previous_files = {}
        previous = None
        if request.expected_snapshot:
            _, previous = _snapshot(lane, request.expected_snapshot)
            if previous['parser_contract'] == manifest['parser_contract'] and not isinstance(request, CodeGitIndex):
                previous_files = {item['path']: item['sha256'] for item in previous['files']}
        reused = sum(previous_files.get(item['path']) == item['sha256'] for item in manifest['files'])
        reused_current = refresh.get('publication') == 'reused_current_snapshot'
        if reused_current:
            stored &= (row['snapshot_id'] == request.expected_snapshot
                and not refresh['parser_contract_changed'])
        else:
            stored &= (refresh.get('publication') == 'new_snapshot'
                and {key: value for key, value in refresh.items() if key != 'publication'} == manifest.get('refresh')
                and manifest['previous_snapshot'] == request.expected_snapshot)
        with context.store.lane('receipts').connection(read_only=True) as connection:
            receipt = connection.execute("SELECT body_json FROM receipts WHERE receipt_id=? AND kind='code_refresh_observed'",
                (output.result.get('refresh_receipt_id'),)).fetchone()
        if receipt is None or len(receipt[0].encode()) > 65_536:
            stored = False
        else:
            observed = json.loads(receipt[0])
            stored &= all(observed.get(key) == value for key, value in {
                'project_id': context.store.project_id, 'lane_id': request.lane_id, 'snapshot_id': row['snapshot_id'],
                'previous_snapshot': request.expected_snapshot, 'scope_id': row['scope_id'], 'task_id': context.task_id,
                'job_id': context.job_id,
                'plan_revision': context.plan_revision, 'source_bytes_mutated': False, 'refresh': refresh}.items())
        stored &= (refresh['policy'] == 'same_scope_hash_and_parser_contract_v1'
            and refresh['reused_files'] == reused
            and refresh['parser_worker_calls'] + reused + refresh['policy_excluded_files'] == refresh['enumerated_files']
            and len(context.worker_evidence) == refresh['parser_worker_calls'] + (2 if isinstance(request, CodeGitIndex) else 0)
            and all(item['status'] == 'ok' for item in context.worker_evidence))
    else:
        stored = False
    checks = {'code_snapshot_integrity': stored and row['scope_id'] == output.result['scope_id'],
              'code_source_hashes_unchanged': live}
    return [{'check_id': name, 'passed': checks[name], 'evidence': {'snapshot_id': row['snapshot_id'],
            'file_count': len(manifest['files']), 'observation': 'exact_bytes_at_verification'}} for name in context.requested_checks]


def verify_snapshot_records(lane, snapshot, manifest):
    """Reconcile typed tables and FTS against immutable source/fact objects."""
    from .code_parsers import _decode, _line_chunks
    expected = {item['path']: item for item in manifest['files']}
    with lane.connection(read_only=True) as connection:
        files = connection.execute('SELECT sf.path,v.* FROM code_snapshot_file sf JOIN code_file_version v USING(version_id) '
            'WHERE sf.snapshot_id=? ORDER BY sf.path', (snapshot['snapshot_id'],)).fetchall()
        if len(files) != len(expected):
            return False
        for row in files:
            source = expected.get(row['path'])
            if (source is None or row['raw_sha256'] != source['sha256'] or row['facts_digest'] != source['facts_digest']
                    or row['size_bytes'] != source['size_bytes'] or row['parser_state'] != source['parser_state']
                    or row['parser_contract'] != snapshot['parser_contract']):
                return False
            file_id = sha256_bytes(canonical_json_bytes([snapshot['repo_id'], row['path']]))
            version_id = sha256_bytes(canonical_json_bytes([file_id, source['sha256'], snapshot['parser_contract']]))
            if row['file_id'] != file_id or row['version_id'] != version_id:
                return False
            facts = json.loads(lane.read_object(source['facts_digest']))
            for kind, table in FACT_TABLES.items():
                records = connection.execute('SELECT payload_json FROM ' + table + ' WHERE version_id=? ORDER BY ordinal', (version_id,)).fetchall()
                if [json.loads(record[0]) for record in records] != facts[kind]:
                    return False
            raw = lane.read_object(source['sha256'])
            text, encoding = _decode(raw)
            if (len(raw) != source['size_bytes'] or row['encoding'] != encoding
                    or len(facts['parser_receipt']) != 1
                    or facts['parser_receipt'][0]['status'] != row['parser_state']
                    or facts['parser_receipt'][0]['language'] != row['language']):
                return False
            expected_chunks = list(_line_chunks(text or '', lines_per_chunk=80, overlap=8))
            chunks = connection.execute('SELECT c.*,f.text_content FROM code_chunk c JOIN code_chunk_fts f USING(chunk_id) '
                'WHERE c.version_id=? ORDER BY c.ordinal', (version_id,)).fetchall()
            if len(chunks) != len(expected_chunks):
                return False
            for chunk, (ordinal, first, last, text) in zip(chunks, expected_chunks, strict=True):
                if ((chunk['ordinal'], chunk['start_line'], chunk['end_line'], chunk['text_content']) != (ordinal, first, last, text)
                        or lane.read_object(chunk['content_object']) != text.encode('utf-8')):
                    return False
        edges = connection.execute('SELECT from_path,import_target,to_path,line_number,resolution FROM code_import_edge WHERE snapshot_id=? LIMIT 32769',
                                   (snapshot['snapshot_id'],)).fetchall()
        if {tuple(row) for row in edges} != snapshot_relations(lane, manifest):
            return False
    return True


def read_current(store, lane_id):
    lane = code_lane(store, lane_id)
    if lane is None:
        return {'scopes': [], 'initialized': False}
    read_compatibility(lane, code_migrations(lane_id))
    with lane.connection(read_only=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='code_current'").fetchone():
            return {'scopes': [], 'initialized': False}
        rows = connection.execute('SELECT s.* FROM code_current c JOIN code_snapshot s USING(snapshot_id) '
            f'WHERE {active_selector_sql(lane)} ORDER BY s.created_at DESC LIMIT 129').fetchall()
    return {'scopes': [dict(row) for row in rows[:128]], 'truncated': len(rows) > 128, 'initialized': True,
            'source_currentness': 'not_checked_by_metadata_read'}


def query_code(store, request):
    lane = code_lane(store, request.lane_id)
    with bounded_project_read(store.root, time.monotonic() + 5):
        snapshot, manifest = _snapshot(lane, request.snapshot_id)
        parameters = [request.snapshot_id]
        scope = ' WHERE sf.snapshot_id=?'
        if request.path_prefix is not None:
            prefix = _relative(request.path_prefix)
            scope += ' AND (sf.path=? OR substr(sf.path,1,?)=?)'
            parameters += [prefix, len(prefix.rstrip('/') + '/'), prefix.rstrip('/') + '/']
        if request.collection == 'text':
            if request.query is None:
                raise LaneError('CODE_QUERY_REQUIRED', 'Provide text to search the selected Code snapshot.')
            tokens = re.findall(r'\w+', request.query, re.UNICODE)
            if not tokens or len(tokens) > 32:
                raise LaneError('CODE_QUERY_INVALID', 'Use one to thirty-two literal search terms.')
            expression = (' AND ' if request.match_mode == 'all' else ' OR ').join('"' + token.replace('"', '""') + '"' for token in tokens)
            sql = ('SELECT sf.path,c.version_id,c.ordinal,c.chunk_id,c.start_line,c.end_line,c.content_object,'
                'code_chunk_fts.text_content AS fts_text,bm25(code_chunk_fts) AS rank '
                'FROM code_chunk_fts JOIN code_chunk c ON c.chunk_id=code_chunk_fts.chunk_id '
                'JOIN code_snapshot_file sf ON sf.version_id=c.version_id' + scope + ' AND code_chunk_fts MATCH ? ORDER BY rank,sf.path,c.ordinal')
            parameters.append(expression)
        elif request.collection == 'files':
            sql = 'SELECT sf.path,v.* FROM code_snapshot_file sf JOIN code_file_version v USING(version_id)' + scope + ' ORDER BY sf.path'
        elif request.collection == 'changes':
            sql = 'SELECT sf.* FROM code_change sf' + scope + ' ORDER BY sf.path'
        else:
            kind = {'symbols': 'symbol', 'imports': 'import', 'calls': 'call', 'routes': 'route',
                    'dependencies': 'dependency', 'receipts': 'parser_receipt', 'diagnostics': 'parser_diagnostic'}[request.collection]
            sql = 'SELECT sf.path,f.* FROM ' + FACT_TABLES[kind] + ' f JOIN code_snapshot_file sf USING(version_id)' + scope
            if request.query:
                sql += ' AND instr(lower(f.name),lower(?))>0'
                parameters.append(request.query)
            sql += ' ORDER BY sf.path,f.ordinal'
        with lane.connection(read_only=True) as connection:
            rows = connection.execute(sql + ' LIMIT ? OFFSET ?', [*parameters, request.limit + 1, request.offset]).fetchall()
        values, used, truncated = [], 0, False
        source_files = {item['path']: item for item in manifest['files']}
        old_files = {}
        if request.collection == 'changes' and manifest['previous_snapshot']:
            _, prior = _snapshot(lane, manifest['previous_snapshot'])
            old_files = {item['path']: item['sha256'] for item in prior['files']}
        source_cache, fact_cache, read_bytes = {}, {}, 0
        for row in rows:
            value = dict(row)
            if request.collection != 'changes':
                source = source_files.get(value['path'])
                if source is None:
                    raise LaneError('CODE_QUERY_INTEGRITY', 'A query row points outside its exact Code snapshot.')
                file_id = sha256_bytes(canonical_json_bytes([snapshot['repo_id'], value['path']]))
                version_id = sha256_bytes(canonical_json_bytes([file_id, source['sha256'], snapshot['parser_contract']]))
                if value['version_id'] != version_id:
                    raise LaneError('CODE_QUERY_INTEGRITY', 'A query row points to another Code file version.')
            if 'payload_json' in value:
                value['fact'] = json.loads(value.pop('payload_json'))
                if source['facts_digest'] not in fact_cache:
                    raw = lane.read_object(source['facts_digest'])
                    read_bytes += len(raw)
                    fact_cache[source['facts_digest']] = json.loads(raw)
                facts = fact_cache[source['facts_digest']][kind]
                if (not 0 <= value['ordinal'] < len(facts) or facts[value['ordinal']] != value['fact']
                        or value['name'] != fact_name(value['fact'])
                        or value['start_line'] != (value['fact'].get('start_line', value['fact'].get('line_number', 1)) or 1)
                        or value['record_id'] != sha256_bytes(canonical_json_bytes([version_id, kind, value['ordinal']]))):
                    raise LaneError('CODE_QUERY_INTEGRITY', 'The queried parser fact differs from its immutable result.')
            if request.collection == 'text':
                value['text'] = lane.read_object(value['content_object']).decode('utf-8')
                if source['sha256'] not in source_cache:
                    from .code_parsers import _decode
                    raw = lane.read_object(source['sha256'])
                    read_bytes += len(raw)
                    text, _ = _decode(raw)
                    source_cache[source['sha256']] = (text or '').splitlines(keepends=True)
                lines = source_cache[source['sha256']]
                first, last = value['ordinal'] * 72 + 1, min(len(lines), value['ordinal'] * 72 + 80)
                if (value['ordinal'] < 0 or value['start_line'] != first or value['end_line'] != last
                        or value['text'] != ''.join(lines[first - 1:last]) or value.pop('fts_text') != value['text']
                        or value['chunk_id'] != sha256_bytes(canonical_json_bytes([version_id, value['ordinal']]))):
                    raise LaneError('CODE_QUERY_INTEGRITY', 'The queried chunk differs from its exact source line range.')
            if request.collection == 'files' and (value['raw_sha256'] != source['sha256'] or value['facts_digest'] != source['facts_digest']
                    or value['size_bytes'] != source['size_bytes'] or value['parser_state'] != source['parser_state']):
                raise LaneError('CODE_QUERY_INTEGRITY', 'The queried file metadata differs from its immutable snapshot.')
            if request.collection == 'changes':
                before, after = old_files.get(value['path']), source_files.get(value['path'], {}).get('sha256')
                expected_kind = 'added' if before is None else 'deleted' if after is None else 'unchanged' if before == after else 'modified'
                if ((before is None and after is None) or (value['before_sha256'], value['after_sha256'], value['change_kind']) != (before, after, expected_kind)):
                    raise LaneError('CODE_QUERY_INTEGRITY', 'The change record differs from its exact before and after snapshots.')
            if read_bytes > 16_777_216:
                raise LaneError('CODE_QUERY_READ_BUDGET', 'Select a smaller page of Code records.')
            size = len(json_text(value).encode())
            if len(values) == request.limit or used + size > request.max_bytes - 1024:
                if not values:
                    raise LaneError('CODE_ROW_BUDGET', 'Increase max_bytes to read this Code record.')
                truncated = True
                break
            values.append(value)
            used += size
        return {'snapshot_id': request.snapshot_id, 'collection': request.collection, 'rows': values,
                'next_offset': request.offset + len(values) if truncated else None,
                'mutation_performed': False, 'refresh_performed': False}


def impact_code(store, request):
    lane = code_lane(store, request.lane_id)
    with bounded_project_read(store.root, time.monotonic() + 5):
        _, manifest = _snapshot(lane, request.snapshot_id)
        relations = snapshot_relations(lane, manifest)
        available = {row['path'] for row in manifest['files']}
        selected = {_relative(path) for path in request.paths}
        if not selected <= available:
            raise LaneError('CODE_IMPACT_SOURCE_MISSING', 'Every impact seed must belong to the selected snapshot.')
        seen, frontier, edges, truncated = set(selected), set(selected), [], False
        column = 2 if request.direction == 'dependents' else 0
        for depth in range(1, request.max_depth + 1):
            next_frontier = set()
            for path in sorted(frontier):
                rows = sorted((row for row in relations if row[column] == path and row[4] == 'unique_static_path'),
                              key=lambda row: (row[0], row[2], row[3]))
                for item in rows:
                    row = dict(zip(('from_path', 'import_target', 'to_path', 'line_number', 'resolution'), item, strict=True))
                    target = row['from_path'] if request.direction == 'dependents' else row['to_path']
                    if len(edges) >= request.max_edges or (target not in seen and len(seen) >= request.max_files):
                        truncated = True
                        continue
                    edges.append({'snapshot_id': request.snapshot_id, **row, 'depth': depth})
                    if target not in seen:
                        seen.add(target)
                        next_frontier.add(target)
            frontier = next_frontier
            if not frontier:
                break
        if frontier:
            truncated = True
        unresolved = sum(row[4] != 'unique_static_path' for row in relations)
        return {'snapshot_id': request.snapshot_id, 'seed_paths': sorted(selected), 'files': sorted(seen),
                'edges': edges, 'truncated': truncated, 'unresolved_or_ambiguous_imports': unresolved,
                'basis': 'static_unique_literal_import_paths', 'runtime_dependency_completeness': False}


def read_code(store, request):
    lane = code_lane(store, request.lane_id)
    _, manifest = _snapshot(lane, request.snapshot_id)
    path = _relative(request.filename)
    row = next((item for item in manifest['files'] if item['path'] == path), None)
    if row is None:
        raise LaneError('CODE_FILE_MISSING', 'Select a file in the exact Code snapshot.')
    from .code_parsers import _decode
    text, encoding = _decode(lane.read_object(row['sha256']))
    if text is None:
        raise LaneError('CODE_BINARY_SOURCE', 'This file has exact bytes but no supported text encoding.')
    lines = text.splitlines(keepends=True)
    excerpt = ''.join(lines[request.start_line - 1:request.start_line - 1 + request.line_count])
    if len(excerpt.encode('utf-8')) > request.max_bytes - 1024:
        raise LaneError('CODE_ROW_BUDGET', 'Select fewer lines or a larger excerpt byte budget.')
    return {'snapshot_id': request.snapshot_id, 'path': path, 'sha256': row['sha256'], 'encoding': encoding,
            'start_line': request.start_line, 'end_line': min(len(lines), request.start_line + request.line_count - 1),
            'text': excerpt, 'total_lines': len(lines), 'source': 'immutable_lane_bytes'}


def _code_view_selection(engine, store):
    from .artifact_contract import LaneArtifacts
    return LaneArtifacts(engine, store).current_selection('local_code.relationships')


def _code_refresh_request(manifest, snapshot_id):
    if 'capture_limits' not in manifest or 'source_route' not in manifest:
        raise LaneError('CODE_REFRESH_PROVENANCE_REQUIRED', 'Reindex this development snapshot with current scope and capture provenance before editing.')
    return CodeIndex(paths=manifest['paths'], expected_snapshot=snapshot_id, **manifest['capture_limits'])


def _code_source_guard(context, request, selection):
    if selection is None:
        return None
    from .source_routing import SourceRouteGuard, SourceRouteSelection
    execution = context.execution
    return SourceRouteGuard(execution.guard, SourceRouteSelection.model_validate(selection), request.model_dump(mode='json'),
        consumer_spec=execution.guard.engine.registry.get('code_index'))


def _affected_code_scopes(store, request, *, check=lambda: None, historical_primary=False):
    """Read a bounded current Local Code catalog, including parent-source scope.

    A routed-out input can still belong to a scope's full Sources observation.
    Its source receipt must advance even when its parser facts are unchanged.
    GitHub Code has separate immutable checkpoint ownership and is not selected.
    """
    from .source_routing import load_route
    lane = code_lane(store, 'local_code')
    selected, primary = _snapshot(lane, request.snapshot_id)
    candidate = Path(request.filename)
    if not candidate.is_absolute():
        candidate = store.source_root / candidate
    if '..' in candidate.parts or not candidate.is_relative_to(store.source_root):
        raise LaneError('CODE_PATH_INVALID', 'Select a file inside this project source root.')
    with lane.connection(read_only=True) as connection:
        totals = connection.execute('SELECT COUNT(*),COALESCE(SUM(o.size_bytes),0) '
            'FROM code_current c JOIN code_snapshot s ON s.snapshot_id=c.snapshot_id '
            f'JOIN objects o ON o.digest=s.manifest_object WHERE {active_selector_sql(lane)}').fetchone()
        if totals[0] > 128 or totals[1] > 16_777_216:
            raise LaneError('CODE_REFRESH_CATALOG_BUDGET', 'Select a project with at most 128 current Code scopes and 16 MiB of scope metadata for automatic edit refresh.')
        current = [dict(row) for row in connection.execute(f'SELECT c.* FROM code_current c WHERE {active_selector_sql(lane)} ORDER BY scope_id LIMIT 129')]
    if len(current) != totals[0]:
        raise LaneError('CODE_SNAPSHOT_INTEGRITY', 'The current Code catalog has a missing manifest binding.')
    if not historical_primary and not any(row['scope_id'] == selected['scope_id'] and row['snapshot_id'] == request.snapshot_id for row in current):
        raise LaneError('CODE_SNAPSHOT_CHANGED', 'Edit only the exact current snapshot for this source scope.')
    affected, routes, route_bytes = [], {}, 0
    for item in current:
        check()
        row, manifest = (selected, primary) if item['snapshot_id'] == request.snapshot_id else _snapshot(lane, item['snapshot_id'])
        if row['scope_id'] != item['scope_id'] or manifest['source_root'] != str(store.source_root):
            raise LaneError('CODE_SNAPSHOT_INTEGRITY', 'The current scope differs from its source-root binding.')
        covered = any(candidate.is_relative_to(store.source_root / path) for path in manifest['paths'])
        parent = manifest.get('source_observation_route_id')
        if not covered and parent:
            if parent not in routes:
                with store.lane('sources').connection(read_only=True) as connection:
                    size = connection.execute('SELECT size_bytes FROM objects WHERE digest=?', (parent,)).fetchone()
                if size is None or not 0 < size[0] <= CODE_MANIFEST_BYTES or route_bytes + size[0] > 16_777_216:
                    raise LaneError('CODE_REFRESH_CATALOG_BUDGET', 'The affected source-routing metadata exceeds its read budget.')
                route_bytes += size[0]
                routes[parent] = load_route(store, parent)
            covered = any((source['kind'] == 'directory' and candidate.is_relative_to(Path(source['resolved_pointer'])))
                or candidate == Path(source['resolved_pointer']) for source in routes[parent]['routes']
                if source['kind'] in {'directory', 'file', 'zip'})
        if covered or item['snapshot_id'] == request.snapshot_id:
            affected.append((row, manifest))
            if len(affected) > 32:
                raise LaneError('CODE_REFRESH_SCOPE_BUDGET', 'One automatic edit can refresh at most 32 affected Local Code scopes.')
    return sorted(affected, key=lambda item: (item[0]['scope_id'] != selected['scope_id'], item[0]['scope_id']))


def _prepare_code_edit_scopes(context, request, path, replacement):
    """Verify every affected scope and source observation before the file effect."""
    from .source_routing import SourceMutationRefresh
    execution, store = context.execution, context.execution.store
    lane = store.lane('local_code')
    relative = path.relative_to(store.source_root).as_posix()
    prepared, sources, union = [], {}, {}
    for row, manifest in _affected_code_scopes(store, request, check=execution._before_more_work):
        refresh_request = _code_refresh_request(manifest, row['snapshot_id'])
        if parser_contract(manifest['syntax_requested']) != manifest['parser_contract']:
            raise LaneError('CODE_PARSER_CONTRACT_CHANGED', 'Refresh every affected Code scope with its current parser before editing.')
        source_guard = _code_source_guard(context, refresh_request, manifest['source_route'])
        _, selected_files, _ = _enumerate(context, refresh_request, source_route=source_guard)
        identities = {}
        for name, file in selected_files.items():
            raw = _read_bytes(file, refresh_request.max_file_bytes)
            identities[name] = {'sha256': sha256_bytes(raw), 'size_bytes': len(raw)}
        if sha256_bytes(canonical_json_bytes(identities)) != manifest['refresh']['source_identity_sha256']:
            raise LaneError('CODE_EDIT_SOURCE_CHANGED', 'An affected Code scope changed after indexing; refresh it before editing.')
        if not verify_snapshot_records(lane, row, manifest):
            raise LaneError('CODE_REUSE_INTEGRITY', 'An affected Code snapshot failed verification before editing.')
        if relative in identities and (len(replacement) > refresh_request.max_file_bytes
                or sum(item['size_bytes'] for item in identities.values()) - identities[relative]['size_bytes']
                    + len(replacement) > refresh_request.max_total_bytes):
            raise LaneError('CODE_TOTAL_BYTE_BUDGET', 'The replacement exceeds an affected Code scope byte budget.')
        parent = manifest.get('source_observation_route_id')
        key = 'route:' + parent if parent else 'paths:' + sha256_bytes(canonical_json_bytes(manifest['paths']))
        if key not in sources:
            source_context = context if not sources else replace(context,
                request_id=str(uuid5(NAMESPACE_URL, str(context.request_id) + '/code-scope/' + key)))
            refresh = SourceMutationRefresh(source_context, manifest['paths'], parent_route_id=parent)
            refresh.expected_after(path, request.expected_sha256, replacement)
            for name, identity in refresh.before.identities.items():
                if name in union and union[name] != identity:
                    raise LaneError('SOURCE_REFRESH_UNEXPECTED_CHANGE', 'Overlapping source observations disagree about the same file.')
                union[name] = identity
            if len(union) > 512 or sum(item['size_bytes'] for item in union.values()) > 33_554_432:
                raise LaneError('SOURCE_CAPTURE_BYTE_BUDGET', 'The combined affected source observations exceed 512 files or 32 MiB.')
            sources[key] = refresh
        prepared.append({'row': row, 'manifest': manifest, 'request': refresh_request,
            'source_key': key, 'parses_replacement': relative in selected_files})
    after_bytes = sum(item['size_bytes'] for item in union.values()) - union[path]['size_bytes'] + len(replacement)
    if after_bytes > 33_554_432:
        raise LaneError('SOURCE_CAPTURE_BYTE_BUDGET', 'The combined replacement and affected sources exceed 32 MiB.')
    return prepared, sources


def apply_code(context, request):
    execution, store = context.execution, context.execution.store
    require_code_calls(execution, 2)
    path = execution.guard.path(request.filename)
    if request.lane_id != 'local_code':
        raise LaneError('CODE_EDIT_LOCAL_LANE_REQUIRED', 'Apply source changes through Local Code; GitHub Code records exact checkpoints.')
    relative = path.relative_to(store.source_root).as_posix()
    ProjectAccess(store).authorize(context.client_id, 'write', path=path)
    ProjectAccess(store).authorize(context.client_id, 'read', path=path)
    lane = store.lane(request.lane_id)
    _, manifest = _snapshot(lane, request.snapshot_id)
    view_selection = _code_view_selection(execution.guard.engine, store)
    indexed = next((item for item in manifest['files'] if item['path'] == relative), None)
    if indexed is None or indexed['sha256'] != request.expected_sha256:
        raise LaneError('CODE_EDIT_SNAPSHOT_MISMATCH', 'The exact file hash must belong to the selected Code snapshot.')
    original = _read_bytes(path, 1_048_576)
    if sha256_bytes(original) != request.expected_sha256:
        raise LaneError('CODE_EDIT_SOURCE_CHANGED', 'The source changed after this Code snapshot; refresh it before editing.')
    # This operation's natural format is UTF-8. Other encodings require an
    # explicit conversion operation, not a silent replacement.
    try:
        original.decode('utf-8')
    except UnicodeError:
        raise LaneError('CODE_EDIT_ENCODING', 'This replacement operation requires a UTF-8 source.') from None
    replacement = request.replacement_utf8.encode('utf-8')
    from .source_policy import content_exclusion_reason
    if content_exclusion_reason(replacement):
        raise LaneError('CODE_EDIT_EXCLUDED_CONTENT', 'The replacement is outside the admitted source-content policy.')
    new_hash = sha256_bytes(replacement)
    if new_hash == request.expected_sha256:
        raise LaneError('CODE_EDIT_UNCHANGED', 'The replacement has the same bytes as the selected source.')
    scopes, source_refreshes = _prepare_code_edit_scopes(context, request, path, replacement)
    variants = list(dict.fromkeys(item['manifest']['syntax_requested'] for item in scopes if item['parses_replacement']))
    # Reuse one attributed parser result per distinct syntax choice across
    # overlapping scopes. Each scope keeps its own source and parser contract.
    require_code_calls(execution, 2 + len(variants) + int(bool(view_selection and view_selection['formats'])))
    parsed, worker_indices = {}, {}
    for syntax in variants:
        maximum = min(item['request'].max_file_bytes for item in scopes
            if item['parses_replacement'] and item['manifest']['syntax_requested'] is syntax)
        response = execution.submit('code_parse_content', {'filename': str(path), 'relative_path': relative,
            'max_file_bytes': maximum, 'syntax': syntax,
            'content_base64': base64.b64encode(replacement).decode('ascii')}).result()
        execution.guard.observe(execution)
        if response['status'] != 'ok' or response['result'].get('sha256') != new_hash:
            raise LaneError('CODE_PARSER_FAILED', 'The replacement parser did not return the exact proposed bytes.')
        parsed[syntax], worker_indices[syntax] = response, len(execution.guard.worker_evidence) - 1
    for item in scopes:
        if parser_contract(item['manifest']['syntax_requested']) != item['manifest']['parser_contract']:
            raise LaneError('CODE_PARSER_CONTRACT_CHANGED', 'An affected parser changed before the source effect.')
    parser_workers = tuple(execution.guard.worker_evidence)
    scope_bindings = [{'scope_id': item['row']['scope_id'], 'snapshot_id': item['row']['snapshot_id']} for item in scopes]
    mutation_id = str(uuid4())
    effect = execution.prepare_effect('code:' + mutation_id, 'Replace one exact UTF-8 source file and verify its hash.')
    with execution.lease.coordinated_transaction([request.lane_id, 'receipts']):
        before, after = lane.put_object(original), lane.put_object(replacement)
        with lane.transaction() as connection:
            connection.execute('INSERT INTO code_mutation VALUES(?,?,?,?,?,?,?,?)',
                (mutation_id, request.snapshot_id, relative, before, after, effect, execution.claim.job_id, now()))
        store.append_receipt('code_mutation_prepared', {'mutation_id': mutation_id, 'path': relative,
            'lane_id': request.lane_id, 'before_sha256': before, 'after_sha256': after, 'effect_id': effect,
            'job_id': execution.claim.job_id, 'task_id': execution.task_id, 'plan_revision': execution.plan_revision,
            'affected_scopes': scope_bindings})
    descriptor, temporary = tempfile.mkstemp(prefix='.evidence-lane-', suffix='.tmp', dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        execution._before_more_work()
        ProjectAccess(store).authorize(context.client_id, 'write', path=path)
        reject_links(path, store.source_root)
        if sha256_bytes(_read_bytes(path, 1_048_576)) != before:
            raise LaneError('CODE_EDIT_SOURCE_CHANGED', 'The source changed immediately before replacement; reconcile this prepared effect.')
        os.chmod(temp_path, path.stat().st_mode)
        os.replace(temp_path, path)
        if sha256_bytes(_read_bytes(path, 1_048_576)) != after:
            raise LaneError('CODE_EDIT_VERIFY_FAILED', 'The written source hash was not confirmed; reconcile this effect.')
        with execution.lease.transaction('plan'):
            evidence = execution.plan_store.put_object(canonical_json_bytes({'mutation_id': mutation_id,
                'lane_id': request.lane_id, 'before_sha256': before, 'after_sha256': after,
                'source_hash_verified': True}))
            execution.confirm_effect(effect, evidence)
    finally:
        if temp_path.exists():
            temp_path.unlink()  # Exact private temporary file created by this invocation.
    with execution.lease.coordinated_transaction(['sources', request.lane_id, 'receipts']):
        source_results = {key: refresh.publish(path=path, before_sha256=before, replacement=replacement,
            mutation_id=mutation_id, effect_id=effect) for key, refresh in source_refreshes.items()}
        scope_results = []
        for item in scopes:
            source_result = source_results[item['source_key']]
            previous = item['manifest']
            route_selection = ({**previous['source_route'], 'route_id': source_result['route_id']} if previous['source_route'] else None)
            renewed_guard = _code_source_guard(context, item['request'], route_selection)
            syntax = previous['syntax_requested']
            refreshed = index_code(context, item['request'], syntax=syntax, source_route=renewed_guard,
                source_observation_route_id=source_result['route_id'],
                prepared_files={relative: parsed[syntax]} if item['parses_replacement'] else None)
            execution.guard.observe(execution)
            scope_results.append({'scope_id': item['row']['scope_id'], 'previous_snapshot': item['row']['snapshot_id'],
                'index_refresh': refreshed.model_dump(mode='json'), 'source_refresh': source_result,
                'parser_worker_indices': [worker_indices[syntax]] if item['parses_replacement'] else []})
        from .artifact_contract import LaneArtifacts
        view_result = LaneArtifacts(execution.guard.engine, store).refresh_selected(
            'local_code.relationships', view_selection, execution, actor_id=context.client_id)
        execution._before_more_work()
        from .source_authority import freeze_source_authority
        for refresh in source_refreshes.values():
            final_capture = refresh.capture()
            for spec in refresh.specs:
                freeze_source_authority(spec, capture=final_capture)
            if final_capture.identities != refresh.expected_after(path, before, replacement):
                raise LaneError('SOURCE_REFRESH_UNEXPECTED_CHANGE', 'A source changed before the refreshed lanes could publish.')
        scope_receipt = store.append_receipt('code_mutation_scopes_refreshed', {
            'project_id': store.project_id, 'job_id': execution.claim.job_id, 'task_id': execution.task_id,
            'plan_revision': execution.plan_revision, 'mutation_id': mutation_id, 'effect_id': effect,
            'path': relative, 'before_sha256': before, 'after_sha256': after,
            'policy': 'all_affected_local_code_scopes_v1', 'scopes': scope_results})
    primary = scope_results[0]
    return _result(store, request.lane_id, 'code_apply', {'mutation_id': mutation_id, 'snapshot_id': primary['index_refresh']['result']['snapshot_id'],
        'previous_snapshot': request.snapshot_id,
        'path': relative, 'before_sha256': before, 'after_sha256': after, 'effect_id': effect,
        'source_bytes_mutated': True, 'index_refresh_required': False, 'automatic_replay': False,
        'index_refresh': primary['index_refresh'], 'source_refresh': primary['source_refresh'],
        'parser_workers': list(parser_workers), 'view_refresh': view_result,
        'scope_refreshes': scope_results, 'scope_refresh_receipt': scope_receipt,
        'scope_refresh_policy': 'all_affected_local_code_scopes_v1'})


def _verify_code_scope_refresh(context, scope, parser_workers):
    lane = context.store.lane('local_code')
    _, previous = _snapshot(lane, scope['previous_snapshot'])
    refresh_request = _code_refresh_request(previous, scope['previous_snapshot'])
    indexed = CodeResult.model_validate(scope['index_refresh'])
    indices = scope['parser_worker_indices']
    if (not isinstance(indices, list) or len(indices) > 1
            or any(type(index) is not int or not 0 <= index < len(parser_workers) for index in indices)):
        return False
    checks = verify_code_index(replace(context, requested_checks=('code_snapshot_integrity', 'code_source_hashes_unchanged'),
        worker_evidence=tuple(parser_workers[index] for index in indices)), refresh_request, indexed)
    valid = all(row['passed'] for row in checks)
    source = scope['source_refresh']
    from .source_routing import load_route
    route = load_route(context.store, source['route_id'])
    with context.store.lane('receipts').connection(read_only=True) as connection:
        receipt = connection.execute("SELECT body_json FROM receipts WHERE receipt_id=? AND kind='source_refresh_after_code_edit'",
            (source['receipt_id'],)).fetchone()
    if receipt is None or len(receipt[0].encode()) > 65_536:
        return False
    body = json.loads(receipt[0])
    valid &= all(body.get(key) == value for key, value in {'project_id': context.store.project_id,
        'job_id': context.job_id, 'task_id': context.task_id, 'plan_revision': context.plan_revision,
        'route_id': source['route_id'], 'batch_id': source['batch_id'],
        'parent_route_id': previous.get('source_observation_route_id'),
        'source_identity_sha256': source['source_identity_sha256']}.items())
    _, current = _snapshot(lane, indexed.result['snapshot_id'])
    with lane.connection(read_only=True) as connection:
        selected = connection.execute('SELECT snapshot_id FROM code_current WHERE scope_id=?', (scope['scope_id'],)).fetchone()
    expected_route = ({**previous['source_route'], 'route_id': source['route_id']} if previous['source_route'] else None)
    valid &= (selected is not None and selected[0] == indexed.result['snapshot_id']
        and scope['scope_id'] == current['scope_id'] == previous['scope_id']
        and current['source_observation_route_id'] == source['route_id'] and route['batch_id'] == source['batch_id']
        and source['parent_route_id'] == previous.get('source_observation_route_id')
        and current['source_route'] == expected_route
        and all(current[key] == previous[key] for key in ('paths', 'capture_limits', 'syntax_requested', 'parser_contract')))
    from .source_authority import SourceAuthoritySpec, SourceCaptureBudget, freeze_source_authority
    from .source_routing import digest
    from .source_selection import registered_directory_selection
    capture = SourceCaptureBudget(lambda value: context.source_path(str(value)), lambda: None)
    for row in route['routes']:
        frozen = freeze_source_authority(SourceAuthoritySpec(row['supplied_pointer'], row['ordinal'], row['lane_id'],
            directory_selection=registered_directory_selection(context.store, row['object_id'])), capture=capture)
        valid &= frozen.identity_sha256.lower() == row['identity_sha256'].lower()
    valid &= digest({str(key): value for key, value in sorted(capture.identities.items())}) == source['source_identity_sha256']
    return valid


def verify_code_apply(context, request, output, *, engine=None):
    path = context.source_path(request.filename)
    lane = context.store.lane(request.lane_id)
    after = output.result['after_sha256']
    valid = (output.project_id == context.store.project_id and output.lane_id == request.lane_id
        and sha256_bytes(_read_bytes(path, 1_048_576)) == after
        and sha256_bytes(lane.read_object(after)) == after
        and sha256_bytes(lane.read_object(output.result['before_sha256'])) == request.expected_sha256)
    with lane.connection(read_only=True) as connection:
        mutation = connection.execute('SELECT * FROM code_mutation WHERE mutation_id=?', (output.result['mutation_id'],)).fetchone()
    with context.store.lane('plan').connection(read_only=True) as connection:
        effect = connection.execute('SELECT * FROM jobs_effects WHERE effect_id=?', (output.result['effect_id'],)).fetchone()
    valid &= (mutation is not None and effect is not None and effect['job_id'] == context.job_id
        and effect['state'] == 'confirmed' and mutation['job_id'] == context.job_id)
    if mutation is not None and effect is not None:
        valid &= all(mutation[key] == value for key, value in {'snapshot_id': request.snapshot_id,
            'path': path.relative_to(context.store.source_root).as_posix(), 'before_object': request.expected_sha256,
            'after_object': after, 'effect_id': output.result['effect_id']}.items())
        proof = json.loads(context.store.lane('plan').read_object(effect['evidence_object']))
        valid &= proof == {'mutation_id': output.result['mutation_id'], 'lane_id': request.lane_id,
            'before_sha256': request.expected_sha256, 'after_sha256': after, 'source_hash_verified': True}
    scopes = output.result['scope_refreshes']
    if not isinstance(scopes, list) or not 1 <= len(scopes) <= 32:
        raise LaneError('CODE_REFRESH_SCOPE_INTEGRITY', 'The edit must name its complete bounded affected scope set.')
    workers = output.result['parser_workers']
    valid &= (1 <= len(workers) <= 2 and list(context.worker_evidence[:len(workers)]) == workers
        and output.result['scope_refresh_policy'] == 'all_affected_local_code_scopes_v1'
        and not output.result['index_refresh_required'])
    current = _affected_code_scopes(context.store, request, historical_primary=True)
    valid &= ([row['scope_id'] for row, _ in current] == [scope['scope_id'] for scope in scopes]
        and len({scope['scope_id'] for scope in scopes}) == len(scopes))
    primary = scopes[0]
    valid &= (primary['previous_snapshot'] == request.snapshot_id
        and primary['index_refresh'] == output.result['index_refresh']
        and primary['source_refresh'] == output.result['source_refresh']
        and primary['index_refresh']['result']['snapshot_id'] == output.result['snapshot_id'])
    with context.store.lane('receipts').connection(read_only=True) as connection:
        prepared = connection.execute("SELECT body_json FROM receipts WHERE kind='code_mutation_prepared' "
            "AND json_extract(body_json,'$.mutation_id')=? LIMIT 2", (output.result['mutation_id'],)).fetchall()
        published = connection.execute("SELECT body_json FROM receipts WHERE kind='code_mutation_scopes_refreshed' "
            'AND receipt_id=?', (output.result['scope_refresh_receipt'],)).fetchone()
    if len(prepared) != 1 or len(prepared[0][0].encode()) > 65_536 or published is None or len(published[0].encode()) > CODE_MANIFEST_BYTES:
        valid = False
    else:
        before, committed = json.loads(prepared[0][0]), json.loads(published[0])
        binding = {'job_id': context.job_id, 'task_id': context.task_id, 'plan_revision': context.plan_revision,
            'mutation_id': output.result['mutation_id'], 'effect_id': output.result['effect_id'],
            'path': path.relative_to(context.store.source_root).as_posix(),
            'before_sha256': request.expected_sha256, 'after_sha256': after}
        valid &= all(before.get(key) == value and committed.get(key) == value for key, value in binding.items())
        valid &= (before.get('affected_scopes') == [{'scope_id': item['scope_id'], 'snapshot_id': item['previous_snapshot']} for item in scopes]
            and committed.get('project_id') == context.store.project_id and committed.get('scopes') == scopes
            and committed.get('policy') == output.result['scope_refresh_policy'])
    consumed = set()
    for scope in scopes:
        valid &= _verify_code_scope_refresh(context, scope, workers)
        consumed.update(scope['parser_worker_indices'])
        with context.store.lane('receipts').connection(read_only=True) as connection:
            source = connection.execute('SELECT body_json FROM receipts WHERE receipt_id=?',
                (scope['source_refresh']['receipt_id'],)).fetchone()
        body = json.loads(source[0]) if source is not None and len(source[0].encode()) <= 65_536 else {}
        valid &= body.get('mutation_id') == output.result['mutation_id'] and body.get('effect_id') == output.result['effect_id']
    valid &= consumed == set(range(len(workers)))
    view = output.result['view_refresh']
    if engine is None:
        valid &= view is None and len(context.worker_evidence) == len(workers)
    else:
        from .artifact_contract import LaneArtifacts
        view_valid, graph_workers = LaneArtifacts(engine, context.store).verify_refreshed('local_code.relationships', view)
        valid &= view_valid and len(context.worker_evidence) == len(workers) + graph_workers
    return [{'check_id': name, 'passed': valid, 'evidence': {'mutation_id': output.result['mutation_id'],
        'after_sha256': after, 'snapshot_id': output.result['snapshot_id'], 'affected_scopes': len(scopes),
        'source_route_id': output.result['source_refresh']['route_id'],
        'scope_refresh_receipt': output.result['scope_refresh_receipt'], 'index_refresh_required': False}}
        for name in context.requested_checks]



def register_code_actions(engine):
    from .artifact_contract import SelectedViewRefresh

    def indexed(context, request):
        return index_code(context, request)

    def syntax(context, request):
        return index_code(context, request, syntax=True)

    def git(context, request):
        return index_code(context, request, git_checkpoint=True)

    for action, handler, tools, workflow in (
        ('code_index', indexed, ('Python', 'SQLite_FTS5_BM25', 'Python_structural_parser'), 'manage-project-sources'),
        ('code_refresh', indexed, ('Python', 'SQLite_FTS5_BM25', 'Python_structural_parser'), 'refresh-project-evidence'),
        ('code_index_syntax', syntax, ('Python', 'SQLite_FTS5_BM25', 'TreeSitter_LanguagePack'), 'manage-project-sources')):
        engine.registry.register(ActionSpec(action, 'Index bounded exact source bytes and attributed parser facts in a selected Code lane.',
            CodeIndex, CodeResult, handler, permission='write', mutates=True, profile='code', workflow=workflow,
            requires_delta=True, path_fields=('paths',), source_lanes=('local_code',),
            materialization=SourceMaterialization('paths', 32) if action == 'code_index' else None, worker_operations=('code_parse_file',),
            verification_checks=('code_snapshot_integrity', 'code_source_hashes_unchanged'), verifier=verify_code_index,
            tool_routes=(ToolRoute(action + '.registered_parser', handler, tools,
                systems=('Windows',) if action == 'code_index_syntax' else ('Windows', 'Darwin', 'Linux')),)))
    for action, workflow in (('code_index_git', 'manage-project-sources'), ('code_refresh_git', 'refresh-project-evidence')):
        engine.registry.register(ActionSpec(action, 'Index exact granted local bytes at the selected clean Sources Git checkpoint.',
            CodeGitIndex, CodeResult, git, permission='write', mutates=True, profile='code', workflow=workflow,
            requires_delta=True, path_fields=('paths',), source_lanes=('github_code',),
            materialization=SourceMaterialization('paths', 32, ('.',)) if action == 'code_index_git' else None, worker_operations=('code_parse_git_blob', 'code_git_checkpoint'),
            verification_checks=('code_snapshot_integrity', 'code_source_hashes_unchanged'), verifier=verify_code_index,
            tool_routes=(ToolRoute(action + '.git_checkpoint', git, ('Python', 'SQLite_FTS5_BM25', 'Python_structural_parser', 'Git')),)))

    def query_handler(function, action):
        def handler(context, request):
            store = engine.directory.open(context.project_id)
            with project_snapshot(store.root):
                return _result(store, request.lane_id, action, function(store, request))
        return handler

    for action, model, function, description in (
        ('code_current', CodeSelection, lambda s, r: read_current(s, r.lane_id), 'Read current Code scope identities without refreshing source bytes.'),
        ('code_query', CodeQuery, query_code, 'Search one exact Code snapshot with bounded FTS5/BM25 or typed fact queries.'),
        ('code_read', CodeRead, read_code, 'Read an attributed line excerpt from exact indexed source bytes.'),
        ('code_impact', CodeImpact, impact_code, 'Follow bounded static import dependencies or dependents in one Code snapshot.')):
        engine.registry.register(ActionSpec(action, description, model, CodeResult, query_handler(function, action),
            profile='code', workflow='manage-project-sources', queryable_in_delta=True, cross_project_read=True,
            studio_read=True, read_migrations=(*code_migrations('local_code'), *code_migrations('github_code')),
            search=SearchRoute(('local_code', 'github_code'), 'rows', 'code_current', 'scopes', 'text', 'any', rerank_text='text') if action == 'code_query' else None))
    def edit_route(syntax, render):
        def applicable(context, request):
            store = engine.directory.open(context.project_id)
            scopes = _affected_code_scopes(store, request)
            view = _code_view_selection(engine, store)
            return any(manifest['syntax_requested'] for _, manifest in scopes) is syntax and bool(view and view['formats']) is render
        return applicable

    edit_routes = tuple(ToolRoute('code_apply.' + ('syntax' if syntax else 'structural') + ('_export' if render else ''),
        apply_code, ('Python', 'SQLite_FTS5_BM25', 'Python_structural_parser', *(('TreeSitter_LanguagePack',) if syntax else ()),
            *(('LangGraph_Mermaid_engine', 'Python_Graphviz_DOT_engine', 'rustworkx') if render else ())),
        view_refresh=SelectedViewRefresh(engine, 'local_code.relationships'), systems=('Windows',) if syntax else ('Windows', 'Darwin', 'Linux'), applicable=edit_route(syntax, render),
        worker_operations=('code_parse_content', *(('render_lane_view',) if render else ())))
        for syntax, render in ((False, False), (False, True), (True, False), (True, True)))

    def verify_edit(context, request, output):
        return verify_code_apply(context, request, output, engine=engine)

    engine.registry.register(ActionSpec('code_apply', 'Parse and replace one exact UTF-8 source file, then refresh Sources, every affected current Local Code scope and existing consumer exports.',
        CodeApply, CodeResult, apply_code, permission='write', mutates=True, requires_delta=True,
        profile='code', workflow='execute-project-plan', path_fields=('filename',),
        worker_operations=('code_parse_content', 'render_lane_view'), tool_routes=edit_routes,
        verification_checks=('code_replacement_hash_verified',), verifier=verify_edit))
    from .code_views import register_code_views
    register_code_views(engine)
    from .code_semantic import register_semantic_actions
    register_semantic_actions(engine)
