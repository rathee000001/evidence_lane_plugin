"""Bounded current-worktree and exact Git-index fingerprint observations.

These are source observations, never authority to execute an old PV lifecycle.
Tracked files, index blobs, explicitly selected paths and untracked bytes have
different meanings. A measured observation is not an atomic filesystem snapshot.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from pathlib import Path, PurePosixPath

from .bounded_io import IOBudget
from .errors import LaneError
from .git_adapter import workflow_git
from .hashing import canonical_json_bytes, sha256_bytes
from .storage import reject_links

_OBJECT_ID = re.compile(r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')
_STABLE_FIELDS = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')


def _require(condition, code, message):
    if not condition:
        raise LaneError(code, message)


def _relative(value):
    _require(isinstance(value, str) and bool(value), 'SOURCE_FINGERPRINT_PATH_INVALID',
        'Select an exact project-relative file path.')
    path = PurePosixPath(value)
    _require(not path.is_absolute() and path.as_posix() == value and value != '.'
        and '..' not in path.parts and not any(character in value for character in ('\\', ':', '\x00')),
        'SOURCE_FINGERPRINT_PATH_INVALID', 'Paths must be canonical and cannot escape the selected source root.')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        raise LaneError('SOURCE_FINGERPRINT_PATH_ENCODING', 'This fingerprint route requires UTF-8 source filenames.') from None
    return value


def _root(repository):
    root = Path(os.path.abspath(repository))
    reject_links(root, Path(root.anchor))
    _require(root.is_dir(), 'SOURCE_FINGERPRINT_ROOT_REQUIRED', 'Select an existing ordinary source directory.')
    return root


class _Observation:
    def __init__(self, root, *, budget=None, tick=None, authorize=None, max_seconds=60):
        self.root, self.budget = root, budget or IOBudget()
        self.tick, self.authorize = tick, authorize
        self.started, self.max_seconds = time.monotonic(), max_seconds
        _require(0 < max_seconds <= 300 and self.budget.consumed_file_count == 0
            and self.budget.consumed_bytes == 0, 'SOURCE_FINGERPRINT_BUDGET_INVALID',
            'Use a fresh finite fingerprint budget.')

    def boundary(self, path, *, inspect_path=True):
        if self.tick:
            self.tick()
        if self.authorize:
            self.authorize(path)
        if inspect_path:
            reject_links(path.parent, self.root)
        _require(time.monotonic() - self.started <= self.max_seconds,
            'SOURCE_FINGERPRINT_TIME_BUDGET', 'The bounded source fingerprint exceeded its observation time.')


def _same(left, right):
    return all(getattr(left, field) == getattr(right, field) for field in _STABLE_FIELDS)


def _measure_file(path, observation):
    observation.boundary(path)
    try:
        before = path.lstat()
    except FileNotFoundError:
        observation.budget.reserve(size_bytes=0)
        return {'state': 'MISSING_WORKTREE_PATH', 'current_sha256': None, 'current_bytes': 0}
    if stat.S_ISLNK(before.st_mode):
        raw = os.readlink(path).encode('utf-8')
        observation.budget.reserve(size_bytes=len(raw))
        observation.boundary(path)
        _require(_same(before, path.lstat()) and os.readlink(path).encode('utf-8') == raw,
            'SOURCE_FINGERPRINT_CHANGED', 'A selected link changed during its metadata observation.')
        return {'state': 'CURRENT_WORKTREE_SYMLINK_TARGET_BYTES',
            'current_sha256': sha256_bytes(raw), 'current_bytes': len(raw)}
    reparse = bool(getattr(before, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))
    _require(stat.S_ISREG(before.st_mode) and not reparse,
        'SOURCE_FINGERPRINT_FILE_REQUIRED', 'Select an ordinary file, a literal symlink or an absent path.')
    observation.budget.reserve(size_bytes=before.st_size)
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        # On this Windows Python runtime, lstat and fstat can expose different
        # ctime meanings. Compare identity/size/mtime across APIs, then compare
        # the complete stable fields within each API before and after the read.
        _require(all(getattr(before, field) == getattr(opened, field)
            for field in _STABLE_FIELDS if field != 'st_ctime_ns'), 'SOURCE_FINGERPRINT_CHANGED',
            'The opened file differs from the selected source identity.')
        hasher, count = hashlib.sha256(), 0
        while True:
            observation.boundary(path)
            block = os.read(descriptor, min(65_536, before.st_size - count + 1))
            if not block:
                break
            count += len(block)
            _require(count <= before.st_size, 'SOURCE_FINGERPRINT_CHANGED',
                'A source file grew during its fingerprint observation.')
            hasher.update(block)
        _require(count == before.st_size and _same(opened, os.fstat(descriptor)),
            'SOURCE_FINGERPRINT_CHANGED', 'A source file changed during its fingerprint observation.')
    finally:
        os.close(descriptor)
    observation.boundary(path)
    _require(_same(before, path.lstat()), 'SOURCE_FINGERPRINT_CHANGED',
        'The selected path changed after the descriptor was read.')
    return {'state': 'CURRENT_WORKTREE_FILE_BYTES',
        'current_sha256': hasher.hexdigest().upper(), 'current_bytes': count}


def measure_worktree_paths(repository_path, paths, *, budget=None, tick=None, authorize=None, max_seconds=60):
    """Measure the exact supplied file/deletion scope without enumerating others."""
    root = _root(repository_path)
    observation = _Observation(root, budget=budget, tick=tick, authorize=authorize, max_seconds=max_seconds)
    selected = []
    for value in paths:
        selected.append(_relative(value))
        _require(len(selected) <= observation.budget.max_file_count, 'SOURCE_FINGERPRINT_PATH_BUDGET',
            'The selected fingerprint exceeds its file-count bound.')
    _require(len(selected) == len(set(selected)) == len({root / value for value in selected}), 'SOURCE_FINGERPRINT_PATH_INVALID',
        'Select distinct exact source paths.')
    rows = []
    for relative in sorted(selected):
        try:
            rows.append({'path': relative, **_measure_file(root / relative, observation)})
        except OSError:
            raise LaneError('SOURCE_FINGERPRINT_CHANGED',
                'A selected file became unavailable during its bounded observation.') from None
    body = {'schema': 'evidence-lane.worktree-path-fingerprint.v4',
        'selection': 'EXACT_DECLARED_PROJECT_PATHS', 'entries': rows,
        'path_set_sha256': sha256_bytes(canonical_json_bytes(sorted(selected))),
        'file_manifest_sha256': sha256_bytes(canonical_json_bytes(rows)),
        'path_count': len(rows), 'limits': observation.budget.receipt(),
        'symlink_target_files_read': False, 'filesystem_snapshot_attested': False,
        'source_bytes_mutated': False, 'git_index_mutated': False, 'git_ref_mutated': False}
    return {**body, 'receipt_sha256': sha256_bytes(canonical_json_bytes(body))}


def _tracked_index_rows(root, *, tick=None, max_files=25_000):
    top = workflow_git(root, ['rev-parse', '--show-toplevel'], tick=tick).stdout.strip()
    _require(Path(top).resolve() == root, 'TRACKED_SOURCE_FINGERPRINT_REPOSITORY_REQUIRED',
        'Select the exact Git worktree root.')
    output = workflow_git(root, ['ls-files', '-z', '--stage'], tick=tick).stdout
    rows = []
    for item in filter(None, output.split('\0')):
        try:
            metadata, path = item.split('\t', 1)
            mode, object_id, stage = metadata.split()
        except ValueError:
            raise LaneError('TRACKED_SOURCE_FINGERPRINT_INDEX_INVALID', 'Git returned an invalid index entry.') from None
        _require(stage == '0' and mode in {'100644', '100755', '120000'} and _OBJECT_ID.fullmatch(object_id),
            'TRACKED_SOURCE_FINGERPRINT_INDEX_INVALID', 'Conflicted index entries and submodules need a separate supported source route.')
        rows.append({'path': _relative(path), 'index_mode': mode, 'index_object_id': object_id})
        _require(len(rows) <= max_files, 'SOURCE_FINGERPRINT_PATH_BUDGET',
            'The tracked index exceeds its explicit file-count bound.')
    _require(bool(rows) and len(rows) == len({row['path'] for row in rows}),
        'TRACKED_SOURCE_FINGERPRINT_INDEX_INVALID', 'The tracked index is empty or contains duplicate paths.')
    return sorted(rows, key=lambda row: row['path'])


def tracked_worktree_file_manifest(repository_path, *, budget=None, tick=None, authorize=None, max_seconds=60):
    """Hash current bytes for every selected stage-0 tracked path, including deletion."""
    root = _root(repository_path)
    budget = budget or IOBudget()
    index = _tracked_index_rows(root, tick=tick, max_files=budget.max_file_count)
    measured = measure_worktree_paths(root, [row['path'] for row in index], budget=budget,
        tick=tick, authorize=authorize, max_seconds=max_seconds)
    _require(_tracked_index_rows(root, tick=tick, max_files=budget.max_file_count) == index,
        'SOURCE_FINGERPRINT_INDEX_CHANGED', 'The Git index changed during the source observation.')
    entries = [{**left, **right,
        'state': 'TRACKED_DELETED_IN_WORKTREE' if right['state'] == 'MISSING_WORKTREE_PATH' else right['state']}
        for left, right in zip(index, measured['entries'], strict=True)]
    body = {'schema': 'evidence-lane.git-tracked-worktree-file-manifest.v4', 'status': 'PASS',
        'selection': 'GIT_INDEX_STAGE0_PATH_SET_WITH_CURRENT_WORKTREE_BYTES',
        'tracked_path_count': len(entries),
        'current_file_count': sum(row['state'] == 'CURRENT_WORKTREE_FILE_BYTES' for row in entries),
        'current_symlink_count': sum(row['state'] == 'CURRENT_WORKTREE_SYMLINK_TARGET_BYTES' for row in entries),
        'tracked_deleted_count': sum(row['state'] == 'TRACKED_DELETED_IN_WORKTREE' for row in entries),
        'path_set_sha256': measured['path_set_sha256'],
        'file_manifest_sha256': sha256_bytes(canonical_json_bytes(entries)), 'entries': entries,
        'limits': measured['limits'], 'untracked_paths_included': False, 'ignored_paths_included': False,
        'remote_git_mutated': False, 'git_index_mutated': False, 'git_ref_mutated': False,
        'symlink_target_files_read': False, 'filesystem_snapshot_attested': False}
    return {**body, 'receipt_sha256': sha256_bytes(canonical_json_bytes(body))}


def staged_index_file_manifest(repository_path, *, budget=None, tick=None, max_seconds=60):
    """Hash exact index blobs without reading or converting worktree content."""
    root = _root(repository_path)
    observation = _Observation(root, budget=budget, tick=tick, max_seconds=max_seconds)
    index = _tracked_index_rows(root, tick=tick, max_files=observation.budget.max_file_count)
    entries = []
    for row in index:
        observation.boundary(root / row['path'], inspect_path=False)
        object_id = row['index_object_id']
        size_text = workflow_git(root, ['cat-file', '-s', object_id], tick=tick).stdout.strip()
        _require(size_text.isdecimal(), 'STAGED_FINGERPRINT_OBJECT_INVALID', 'Git returned an invalid blob size.')
        size = int(size_text)
        observation.budget.reserve(size_bytes=size)
        value = workflow_git(root, ['cat-file', 'blob', object_id], tick=tick,
            max_stdout_bytes=size + 1).stdout.encode('utf-8', errors='surrogateescape')
        observation.boundary(root / row['path'], inspect_path=False)
        _require(len(value) == size, 'STAGED_FINGERPRINT_OBJECT_INVALID', 'The selected blob differs from its declared size.')
        entries.append({**row, 'state': 'EXACT_GIT_INDEX_BLOB',
            'staged_sha256': sha256_bytes(value), 'staged_bytes': size,
            'lfs_pointer': value.startswith(b'version https://git-lfs.github.com/spec/v1')})
    _require(_tracked_index_rows(root, tick=tick, max_files=observation.budget.max_file_count) == index,
        'SOURCE_FINGERPRINT_INDEX_CHANGED', 'The Git index changed during its blob observation.')
    body = {'schema': 'evidence-lane.git-staged-index-file-manifest.v4', 'status': 'PASS',
        'selection': 'GIT_INDEX_STAGE0_EXACT_BLOB_BYTES',
        'staged_path_count': len(entries), 'staged_bytes': sum(row['staged_bytes'] for row in entries),
        'lfs_pointer_count': sum(row['lfs_pointer'] for row in entries),
        'path_set_sha256': sha256_bytes(canonical_json_bytes([row['path'] for row in entries])),
        'staged_manifest_sha256': sha256_bytes(canonical_json_bytes(entries)), 'entries': entries,
        'limits': observation.budget.receipt(), 'current_worktree_bytes_substituted': False,
        'untracked_paths_included': False, 'ignored_paths_included': False,
        'git_index_mutated': False, 'git_ref_mutated': False}
    return {**body, 'receipt_sha256': sha256_bytes(canonical_json_bytes(body))}


__all__ = ['measure_worktree_paths', 'staged_index_file_manifest', 'tracked_worktree_file_manifest']
