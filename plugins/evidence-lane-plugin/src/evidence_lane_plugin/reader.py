"""Bounded source reads and published lane comparisons in the original owner.

Code snapshots own exact source bytes. Root PV coordinates recorded lane heads;
its journal is not a historical content database. All operations are read-only.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
from collections import Counter, defaultdict
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .code_parsers import _decode
from .code_profile import DIGEST, CodeSnapshot, _relative, _snapshot, code_lane, sha256_bytes
from .code_profile_schema import code_migrations
from .errors import LaneError
from .hashing import canonical_json_bytes
from .registry import ActionSpec, Contract, FetchRoute
from .storage import STORAGE_LAYOUT, bounded_project_read, json_text
from .universe_snapshot import digest, root_reference

UUID = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
MIME_TYPES = mimetypes.MimeTypes(filenames=())


class Fetch(CodeSnapshot):
    ref_id: str = Field(min_length=6, max_length=1100)
    max_bytes: int = Field(default=256000, ge=1, le=1000000)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    max_lines: int = Field(default=400, ge=1, le=1000)
    byte_offset: int = Field(default=0, ge=0, le=1048576)

    @model_validator(mode='after')
    def range_order(self):
        if self.end_line is not None and self.end_line < (self.start_line or 1):
            raise ValueError('The line range must be ordered')
        if not (self.ref_id.startswith('file:') or re.fullmatch('chunk:[0-9a-f]{64}', self.ref_id)):
            raise ValueError('Use file:<relative path> or chunk:<current chunk digest>')
        return self


class Summary(CodeSnapshot):
    max_read_bytes: int = Field(default=67108864, ge=1024, le=134217728)
    timeout_ms: int = Field(default=10000, ge=100, le=30000)


class RootReference(Contract):
    singleton: Literal[1] = 1
    revision: int = Field(ge=0)
    head_digest: str = Field(pattern=r'^(?:[0-9a-f]{64})?$')
    commit_id: str | None = Field(pattern=UUID)

    @model_validator(mode='after')
    def consistent(self):
        if (self.revision == 0) != (self.head_digest == '' and self.commit_id is None):
            raise ValueError('An initial reference has no digest or commit')
        if self.revision > 0 and (not self.head_digest or self.commit_id is None):
            raise ValueError('A published reference requires both digest and commit')
        return self


class History(Contract):
    max_commits: int = Field(default=128, ge=1, le=2048)
    max_bytes: int = Field(default=1048576, ge=4096, le=2097152)
    timeout_ms: int = Field(default=10000, ge=100, le=30000)


class Diff(History):
    left_root: RootReference
    right_root: RootReference


class ReadResult(Contract):
    project_id: str
    operation: str
    result: dict[str, JsonValue]
    mutation_performed: Literal[False] = False
    refresh_performed: Literal[False] = False


class ReadBudget:
    def __init__(self, maximum, deadline):
        self.remaining, self.deadline = maximum, deadline

    def tick(self):
        if time.monotonic() >= self.deadline:
            raise LaneError('READER_TIMEOUT', 'Reduce the selected read or raise its time budget.')

    def object(self, lane, object_id):
        self.tick()
        path = lane.object_path(object_id)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            raise LaneError('OBJECT_MISSING', 'The addressed content is missing.') from None
        if size > self.remaining:
            raise LaneError('READER_BYTE_BUDGET', 'The selected snapshot exceeds its read budget.')
        raw = lane.read_object(object_id)
        self.remaining -= len(raw)
        if self.remaining < 0:
            raise LaneError('READER_BYTE_BUDGET', 'The selected snapshot exceeds its read budget.')
        self.tick()
        return raw


def source_context(request, manifest):
    return {'lane_id': request.lane_id, 'snapshot_id': request.snapshot_id,
        'source': 'immutable_lane_bytes', 'source_currentness': 'not_checked',
        'scope_id': manifest['scope_id'], 'generation': manifest['generation'],
        'git_reference': manifest['git_reference'], 'byte_source': manifest['byte_source']}


def checked_source(lane, row, budget):
    raw = budget.object(lane, row['sha256'])
    if len(raw) != row['size_bytes']:
        raise LaneError('READER_SOURCE_INTEGRITY', 'Stored source size differs from the exact snapshot.')
    return raw


def truncate_text(text, maximum):
    encoded = text.encode('utf-8')
    # Never introduce a replacement character when a byte budget splits UTF-8.
    return encoded[:maximum].decode('utf-8', errors='ignore'), len(encoded) > maximum


def checked_heads(rows):
    if not isinstance(rows, list) or len(rows) > 512:
        raise ValueError('Bounded lane head list required')
    result = {}
    for row in rows:
        if (set(row) != {'lane_id', 'revision', 'head_digest', 'commit_id'}
                or not re.fullmatch(r'[a-z][a-z0-9_]{0,95}', row['lane_id'])
                or type(row['revision']) is not int or row['revision'] < 1
                or not re.fullmatch(DIGEST, row['head_digest'])
                or not re.fullmatch(UUID, row['commit_id']) or row['lane_id'] in result):
            raise ValueError('Invalid lane head')
        result[row['lane_id']] = row
    if list(result) != sorted(result):
        raise ValueError('Noncanonical lane heads')
    return result


def checked_publication(connection, project_id, current, expected_heads=None):
    row = connection.execute('SELECT phase,body_json FROM root_transaction_journal WHERE commit_id=?',
        (current['commit_id'],)).fetchone()
    try:
        if not row or row['phase'] != 'published' or len(row['body_json'].encode()) > 1048576:
            raise ValueError('Missing bounded publication')
        body = json.loads(row['body_json'])
        if body['project_id'] != project_id or body['layout'] != STORAGE_LAYOUT:
            raise ValueError('Wrong project or storage layout')
        before = RootReference.model_validate(body['before_root']).model_dump()
        prior = checked_heads(body['before_heads'])
        after = checked_heads(body['published_heads'])
        if (before['revision'] + 1 != current['revision']
                or (before['revision'] == 0 and prior)
                or (expected_heads is not None and body['published_heads'] != expected_heads)
                or digest({'project_id': project_id, 'revision': current['revision'],
                    'previous_digest': before['head_digest'], 'lanes': body['published_heads']}) != current['head_digest']):
            raise ValueError('Broken publication ancestry')
        records = body['lanes']
        if not isinstance(records, list) or not 1 <= len(records) <= 512:
            raise ValueError('Bounded changed lane list required')
        expected, seen = dict(prior), set()
        for record in records:
            lane_id = record['lane_id']
            if lane_id in seen or lane_id not in after:
                raise ValueError('Duplicate or missing changed lane')
            seen.add(lane_id)
            for key in ('before_sha256', 'after_sha256'):
                if not re.fullmatch(DIGEST, record[key]):
                    raise ValueError('Invalid database hash')
            old = prior.get(lane_id)
            if old and digest({'lane_id': lane_id, 'database_sha256': record['before_sha256']}) != old['head_digest']:
                raise ValueError('Before database hash differs from prior lane head')
            expected[lane_id] = {'lane_id': lane_id, 'revision': old['revision'] + 1 if old else 1,
                'head_digest': digest({'lane_id': lane_id, 'database_sha256': record['after_sha256']}),
                'commit_id': current['commit_id']}
        if expected != after:
            raise ValueError('Unexplained lane transition')
        return before, body['before_heads'], body['published_heads']
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise LaneError('READER_HISTORY_INTEGRITY', 'The published Root PV history is inconsistent.') from error


class PVReader:
    """Original owner adapted to the selected v4 project and exact references."""

    def __init__(self, store):
        self.store = store

    def fetch(self, request):
        with bounded_project_read(self.store.root, time.monotonic() + 10):
            lane = code_lane(self.store, request.lane_id)
            snapshot, manifest = _snapshot(lane, request.snapshot_id)
            budget = ReadBudget(4194304, time.monotonic() + 10)
            chunk = None
            if request.ref_id.startswith('chunk:'):
                if request.start_line is not None or request.end_line is not None or request.byte_offset:
                    raise LaneError('CHUNK_RANGE_UNSUPPORTED', 'Chunk references already select an exact line range.')
                chunk_id = request.ref_id.removeprefix('chunk:')
                with lane.connection(read_only=True) as connection:
                    rows = connection.execute('SELECT sf.path,c.* FROM code_chunk c JOIN code_snapshot_file sf USING(version_id) '
                        'WHERE sf.snapshot_id=? AND c.chunk_id=? LIMIT 2', (request.snapshot_id, chunk_id)).fetchall()
                if len(rows) != 1:
                    raise LaneError('CHUNK_NOT_FOUND', 'Select an exact chunk in this Code snapshot.')
                chunk, path = dict(rows[0]), rows[0]['path']
            else:
                path = _relative(request.ref_id.removeprefix('file:'))
            source = next((item for item in manifest['files'] if item['path'] == path), None)
            if source is None:
                raise LaneError('CODE_FILE_MISSING', 'Select a file in the exact Code snapshot.')
            raw = checked_source(lane, source, budget)
            text, encoding = _decode(raw)
            first = last = total_lines = None
            next_offset = None
            if chunk is not None:
                lines = (text or '').splitlines(keepends=True)
                version = sha256_bytes(canonical_json_bytes([
                    sha256_bytes(canonical_json_bytes([snapshot['repo_id'], path])),
                    source['sha256'], snapshot['parser_contract']]))
                ordinal = chunk['ordinal']
                first, last = ordinal * 72 + 1, min(len(lines), ordinal * 72 + 80)
                full = ''.join(lines[first - 1:last])
                content_bytes = budget.object(lane, chunk['content_object'])
                if (text is None or ordinal < 0 or first > last or chunk['version_id'] != version
                        or (chunk['start_line'], chunk['end_line']) != (first, last)
                        or chunk['chunk_id'] != sha256_bytes(canonical_json_bytes([version, ordinal]))
                        or content_bytes != full.encode('utf-8')):
                    raise LaneError('READER_CHUNK_INTEGRITY', 'The chunk differs from its exact source lines.')
                content, truncated = truncate_text(full, request.max_bytes)
                representation, total_lines = 'text', len(lines)
            elif text is None:
                if request.start_line is not None or request.end_line is not None:
                    raise LaneError('BINARY_LINE_RANGE_UNSUPPORTED', 'Use byte offsets for binary files.')
                if request.byte_offset > len(raw):
                    raise LaneError('FETCH_OFFSET_INVALID', 'The byte offset exceeds the selected file.')
                selected = raw[request.byte_offset:request.byte_offset + request.max_bytes]
                content, representation = base64.b64encode(selected).decode('ascii'), 'base64'
                end = request.byte_offset + len(selected)
                next_offset = end if end < len(raw) else None
                truncated = request.byte_offset > 0 or end < len(raw)
            else:
                if request.byte_offset:
                    raise LaneError('TEXT_BYTE_OFFSET_UNSUPPORTED', 'Use line windows for text files.')
                lines = text.splitlines(keepends=True)
                total_lines, first = len(lines), request.start_line or 1
                if first > max(total_lines, 1):
                    raise LaneError('FETCH_LINE_RANGE_INVALID', 'The line window starts beyond this file.')
                last = min(request.end_line or max(total_lines, 1), first + request.max_lines - 1, total_lines)
                selected = ''.join(lines[first - 1:last])
                content, byte_truncated = truncate_text(selected, request.max_bytes)
                truncated = byte_truncated or first > 1 or last < total_lines
                representation = 'text'
                if not total_lines:
                    first = last = None
            return {**source_context(request, manifest), 'ref_id': request.ref_id, 'path': path,
                'file_sha256': source['sha256'], 'size_bytes': source['size_bytes'],
                'parser_state': source['parser_state'], 'extension': PurePosixPath(path).suffix.lower(),
                'mime_type': MIME_TYPES.guess_type(path)[0] or 'application/octet-stream',
                'mime_type_basis': 'filename_guess',
                'encoding': encoding, 'representation': representation, 'content': content,
                'content_utf8_bytes': len(content.encode('utf-8')), 'content_sha256': sha256_bytes(
                    base64.b64decode(content) if representation == 'base64' else content.encode('utf-8')),
                'chunk_sha256': chunk['content_object'] if chunk else None,
                'start_line': first, 'end_line': last, 'total_lines': total_lines,
                'byte_offset': request.byte_offset if representation == 'base64' else None,
                'next_byte_offset': next_offset, 'truncated': truncated}

    def project_summary(self, request):
        deadline = time.monotonic() + request.timeout_ms / 1000
        with bounded_project_read(self.store.root, deadline):
            lane = code_lane(self.store, request.lane_id)
            _, manifest = _snapshot(lane, request.snapshot_id)
            budget = ReadBudget(request.max_read_bytes, deadline)
            if len(manifest['files']) > 512:
                raise LaneError('READER_FILE_BUDGET', 'Select a Code snapshot with at most 512 files.')
            counts = Counter(files=len(manifest['files']), bytes=0, chunks=0, symbols=0,
                imports=0, calls=0, routes=0, dependencies=0, receipts=0, diagnostics=0)
            families = defaultdict(lambda: {'files': 0, 'bytes': 0})
            states, largest = Counter(), []
            names = {'symbol': 'symbols', 'import': 'imports', 'call': 'calls', 'route': 'routes',
                'dependency': 'dependencies', 'parser_receipt': 'receipts', 'parser_diagnostic': 'diagnostics'}
            for source in manifest['files']:
                raw = checked_source(lane, source, budget)
                text, _ = _decode(raw)
                facts = json.loads(budget.object(lane, source['facts_digest']))
                for name, target in names.items():
                    counts[target] += len(facts[name])
                lines = len((text or '').splitlines(keepends=True))
                counts['chunks'] += 1 + (max(lines - 80, 0) + 71) // 72 if lines else 0
                counts['bytes'] += len(raw)
                family = PurePosixPath(source['path']).suffix.lower() or '[none]'
                families[family]['files'] += 1
                families[family]['bytes'] += len(raw)
                states[source['parser_state']] += 1
                largest.append({**{key: source[key] for key in ('path', 'size_bytes', 'parser_state')},
                    'extension': PurePosixPath(source['path']).suffix.lower(),
                    'mime_type': MIME_TYPES.guess_type(source['path'])[0] or 'application/octet-stream',
                    'mime_type_basis': 'filename_guess'})
            before = {}
            if manifest['previous_snapshot']:
                _, prior = _snapshot(lane, manifest['previous_snapshot'])
                before = {row['path']: row['sha256'] for row in prior['files']}
            after = {row['path']: row['sha256'] for row in manifest['files']}
            changes = Counter(added=0, modified=0, deleted=0, unchanged=0)
            for path in before.keys() | after.keys():
                kind = ('added' if path not in before else 'deleted' if path not in after
                    else 'unchanged' if before[path] == after[path] else 'modified')
                changes[kind] += 1
            budget.tick()
            return {**source_context(request, manifest), 'counts': dict(counts),
                'families': [{'extension': key, **value} for key, value in sorted(
                    families.items(), key=lambda pair: (-pair[1]['files'], pair[0]))],
                'largest_files': sorted(largest, key=lambda row: (-row['size_bytes'], row['path']))[:10],
                'parser_states': dict(sorted(states.items())), 'changes': dict(changes),
                'previous_snapshot': manifest['previous_snapshot'], 'created_at': manifest['created_at'],
                'read_bytes': request.max_read_bytes - budget.remaining}

    def _history(self, request, minimum=None):
        deadline = time.monotonic() + request.timeout_ms / 1000
        with bounded_project_read(self.store.root, deadline):
            current = RootReference.model_validate(root_reference(self.store)).model_dump()
            head, expected, rows, used = current, None, [], 1024
            with self.store.connection(read_only=True) as connection:
                for _ in range(request.max_commits):
                    if time.monotonic() >= deadline:
                        raise LaneError('READER_TIMEOUT', 'Reduce the Root PV history range.')
                    if current['revision'] == 0:
                        if expected not in (None, []):
                            raise LaneError('READER_HISTORY_INTEGRITY', 'Initial Root PV has published lane heads.')
                        rows.append({'root': current, 'heads': []})
                        return head, rows, False
                    parent, prior_heads, heads = checked_publication(connection, self.store.project_id, current, expected)
                    value = {'root': current, 'heads': heads}
                    used += len(json_text(value).encode())
                    if used > request.max_bytes:
                        if minimum is not None or not rows:
                            raise LaneError('READER_HISTORY_BYTE_BUDGET', 'The selected history exceeds max_bytes.')
                        return head, rows, True
                    rows.append(value)
                    if minimum is not None and current['revision'] <= minimum:
                        return head, rows, True
                    current, expected = parent, prior_heads
                if current['revision'] == 0:
                    if expected:
                        raise LaneError('READER_HISTORY_INTEGRITY', 'Initial Root PV has published lane heads.')
                    rows.append({'root': current, 'heads': []})
                    return head, rows, False
                if minimum is not None:
                    raise LaneError('READER_HISTORY_BUDGET', 'The selected comparison exceeds max_commits from the current head.')
                return head, rows, True

    def history(self, request):
        head, rows, truncated = self._history(request)
        return {'current_root': head, 'publications': rows, 'truncated': truncated,
            'comparison_authority': 'published_root_journal', 'historical_contents_available': False}

    def diff(self, request):
        head, rows, _ = self._history(request, min(request.left_root.revision, request.right_root.revision))
        by_revision = {row['root']['revision']: row for row in rows}
        selected = []
        for reference in (request.left_root, request.right_root):
            row = by_revision.get(reference.revision)
            if row is None or row['root'] != reference.model_dump():
                raise LaneError('READER_ROOT_REFERENCE_MISMATCH', 'Both exact references must belong to the current published ancestry.')
            selected.append({item['lane_id']: item for item in row['heads']})
        left, right = selected
        delta = {'added': [], 'modified': [], 'removed': [], 'unchanged': [], 'republished': []}
        for lane_id in sorted(left.keys() | right.keys()):
            before, after = left.get(lane_id), right.get(lane_id)
            if before is None:
                delta['added'].append({'lane_id': lane_id, 'after': after})
            elif after is None:
                delta['removed'].append({'lane_id': lane_id, 'before': before})
            elif before['head_digest'] != after['head_digest']:
                delta['modified'].append({'lane_id': lane_id, 'before': before, 'after': after})
            else:
                delta['unchanged'].append(lane_id)
                if before != after:
                    delta['republished'].append({'lane_id': lane_id, 'before': before, 'after': after})
        body = {'current_root': head, 'left_root': request.left_root.model_dump(),
            'right_root': request.right_root.model_dump(), 'lane_delta': delta,
            'comparison_authority': 'published_root_journal', 'historical_contents_available': False,
            'comparison_scope': 'recorded_lane_database_heads', 'verified_publications': len(rows)}
        return {**body, 'comparison_sha256': digest(body)}


def register_reader_actions(engine):
    def handler(method, operation):
        def read(context, request):
            store = engine.directory.open(context.project_id)
            result = getattr(PVReader(store), method)(request)
            output = ReadResult(project_id=store.project_id, operation=operation, result=result)
            if len(json_text(output.model_dump()).encode()) > 2097152:
                raise LaneError('READER_OUTPUT_BUDGET', 'Reduce the selected page or read budget.')
            return output
        return read

    for name, model, method, description, profile, workflow in (
        ('fetch', Fetch, 'fetch', 'Fetch exact stored Code file or chunk bytes with bounded text or base64 output.', 'code', 'source-intake'),
        ('pv_summary', Summary, 'project_summary', 'Summarize verified bytes and facts of one exact Code snapshot.', 'code', 'source-intake'),
        ('pv_history', History, 'history', 'Read bounded, verified published Root PV references from current ancestry.', 'projects', 'evi'),
        ('pv_diff', Diff, 'diff', 'Compare exact published Root PV lane database heads without reading historical content.', 'projects', 'evi')):
        engine.registry.register(ActionSpec(name, description, model, ReadResult, handler(method, name),
            profile=profile, workflow=workflow, queryable_in_delta=True, cross_project_read=True, studio_read=True,
            read_migrations=(*code_migrations('local_code'), *code_migrations('github_code')) if profile == 'code' else (),
            fetch=FetchRoute(('local_code', 'github_code'), path_argument='ref_id', path_prefix='file:',
                offset_argument='byte_offset', path_result='path', digest_result='file_sha256',
                size_result='size_bytes') if name == 'fetch' else None))
