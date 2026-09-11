"""Trusted Code worker operations; no source module is imported or executed."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from .code_parsers import parse_code
from .source_policy import content_exclusion_reason
from .storage import reject_links


def parse_file(arguments):
    path = Path(arguments['filename'])
    reject_links(path, Path(path.anchor))
    before = path.stat()
    with path.open('rb') as stream:
        content = stream.read(arguments['max_file_bytes'] + 1)
    after = path.stat()
    if len(content) > arguments['max_file_bytes']:
        raise ValueError('CODE_FILE_BYTE_BUDGET')
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('CODE_SOURCE_CHANGED')
    return _parsed(arguments, content)


def _parsed(arguments, content):
    exclusion = content_exclusion_reason(content)
    if exclusion:
        return {'path': arguments['relative_path'], 'excluded': exclusion}
    parsed = parse_code(arguments['relative_path'], content, syntax=arguments['syntax'])
    return {'path': arguments['relative_path'], 'sha256': hashlib.sha256(content).hexdigest(),
            'size_bytes': len(content), 'content_base64': base64.b64encode(content).decode('ascii'),
            **parsed}


def parse_content(arguments):
    """Parse bounded replacement bytes before their filesystem effect."""
    encoded = arguments['content_base64']
    if len(encoded) > ((arguments['max_file_bytes'] + 2) // 3) * 4:
        raise ValueError('CODE_FILE_BYTE_BUDGET')
    content = base64.b64decode(encoded, validate=True)
    if len(content) > arguments['max_file_bytes']:
        raise ValueError('CODE_FILE_BYTE_BUDGET')
    return _parsed(arguments, content)


def code_worker_operations():
    from .workers import WorkerOperation
    return (WorkerOperation('code_parse_file', __name__, 'parse_file', path_fields=('filename',)),
            WorkerOperation('code_parse_content', __name__, 'parse_content', path_fields=('filename',)),
            WorkerOperation('code_git_checkpoint', __name__, 'git_checkpoint', path_fields=('repository',)),
            WorkerOperation('code_parse_git_blob', __name__, 'parse_git_blob', path_fields=('repository',)),
            WorkerOperation('code_embed_text', 'evidence_lane_plugin.code_semantic', 'embedding_worker', dependencies=('sentence_transformers',)))


def git_checkpoint(arguments):
    from .git_adapter import restoration_git
    repo = Path(arguments['repository'])
    reject_links(repo, Path(repo.anchor))
    top = restoration_git(repo, ['rev-parse', '--show-toplevel']).stdout.strip()
    if Path(top).resolve() != repo.resolve():
        raise ValueError('CODE_GIT_ROOT_REQUIRED')
    head = restoration_git(repo, ['rev-parse', '--verify', 'HEAD']).stdout.strip()
    tree = restoration_git(repo, ['rev-parse', '--verify', 'HEAD^{tree}']).stdout.strip()
    status = restoration_git(repo, ['status', '--porcelain=v2', '-z', '--untracked-files=all']).stdout
    tree_entries = restoration_git(repo, ['ls-tree', '-r', '-z', '--full-tree', head]).stdout.split('\0')
    blobs = {}
    for value in tree_entries:
        if not value:
            continue
        header, path = value.split('\t', 1)
        mode, kind, oid = header.split(' ')
        if kind == 'blob' and mode != '120000':
            blobs[path] = oid
    selected = sorted(blobs)
    if len(selected) > arguments['max_files'] * 40 + 100:
        raise ValueError('CODE_GIT_FILE_BUDGET')
    return {'head_commit_sha': head, 'head_tree_sha': tree, 'clean': not bool(status),
            'tracked_paths': selected, 'blob_ids': blobs, 'remote_contacted': False}


def parse_git_blob(arguments):
    from .git_adapter import restoration_git
    repository = Path(arguments['repository'])
    reject_links(repository, Path(repository.anchor))
    # The engine chooses this immutable object ID from its measured tree;
    # callers cannot supply arbitrary Git options or an executable command.
    oid = arguments['blob_id']
    import re
    if not re.fullmatch(r'[0-9a-f]{40}(?:[0-9a-f]{24})?', oid):
        raise ValueError('CODE_GIT_BLOB_ID_INVALID')
    content = restoration_git(repository, ['cat-file', 'blob', oid]).stdout.encode('utf-8', errors='surrogateescape')
    if len(content) > arguments['max_file_bytes']:
        raise ValueError('CODE_FILE_BYTE_BUDGET')
    algorithm = 'sha1' if len(oid) == 40 else 'sha256'
    observed = hashlib.new(algorithm, b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
    if observed != oid:
        raise ValueError('CODE_GIT_BLOB_HASH_MISMATCH')
    return {**_parsed(arguments, content), 'git_blob_id': oid}
