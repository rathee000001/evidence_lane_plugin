"""Read-only, hash-verified model, grammar and runtime assets from the shared installation.

The full bundle installer owns acquisition and emits this contract. Reading it
does not install, download, create a cache, or make a runtime-attestation claim.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .errors import LaneError
from .hashing import canonical_json_bytes
from .installation_layout import studio_installation
from .storage import reject_links

ASSET_SCHEMA = 'evidence-lane.shared-tool-assets.v4'


def resolve_shared_asset(asset_id, *, installation=None):
    installation = installation or studio_installation()
    root = installation.active_root
    manifest = root / 'toolchains/asset-installation.v4.json'
    reject_links(manifest, root)
    if not manifest.is_file():
        raise LaneError('SHARED_ASSETS_NOT_INSTALLED', 'The required shared model or grammar assets have not been installed.')
    with manifest.open('rb') as stream:
        content = stream.read(16_777_217)
    if len(content) > 16_777_216:
        raise LaneError('SHARED_ASSET_BUDGET', 'The shared asset manifest exceeds its byte budget.')
    value = json.loads(content)
    expected = value.pop('receipt_sha256', None)
    if (value.get('schema') != ASSET_SCHEMA or value.get('status') != 'verified'
            or hashlib.sha256(canonical_json_bytes(value)).hexdigest() != expected):
        raise LaneError('SHARED_ASSET_MANIFEST_INVALID', 'The shared asset manifest failed its integrity check.')
    records = value.get('assets', [])
    if not isinstance(records, list) or len(records) > 128 or len({row['asset_id'] for row in records}) != len(records):
        raise LaneError('SHARED_ASSET_MANIFEST_INVALID', 'The shared asset manifest must contain distinct bounded identities.')
    row = next((record for record in records if record['asset_id'] == asset_id), None)
    if row is None or row.get('status') != 'verified':
        raise LaneError('SHARED_ASSET_NOT_INSTALLED', 'The selected asset has no verified installation record.')

    def relative(value):
        path = Path(value)
        if path.is_absolute() or path.drive or '..' in path.parts or ':' in str(path) or '\x00' in str(path):
            raise LaneError('SHARED_ASSET_PATH_INVALID', 'An asset path must remain inside the shared installation.')
        return path

    folder = root / relative(row['path'])
    reject_links(folder, root)
    if not folder.is_dir():
        raise LaneError('SHARED_ASSET_MISSING', 'The selected shared asset directory is missing.')
    files = row.get('files', [])
    if not isinstance(files, list) or not 1 <= len(files) <= 32768 or len({file['path'] for file in files}) != len(files):
        raise LaneError('SHARED_ASSET_MANIFEST_INVALID', 'Each asset requires a bounded distinct file-hash manifest.')
    for file in files:
        path = folder / relative(file['path'])
        reject_links(path, root)
        if not re.fullmatch(r'[0-9a-f]{64}', file['sha256']) or not path.is_file() or path.stat().st_size != file['bytes']:
            raise LaneError('SHARED_ASSET_INTEGRITY', 'An installed asset file differs from its manifest.')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != file['sha256']:
                raise LaneError('SHARED_ASSET_INTEGRITY', 'An installed asset file differs from its recorded hash.')
    # Model loaders discover configuration/weight files by name. An added file
    # must not bypass the pinned manifest. HF's download metadata is inert and
    # is the only excluded subtree; runtime assets never load from .cache.
    expected_paths = {relative(file['path']).as_posix() for file in files}
    observed, visited, stack = set(), 0, [folder]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                if visited > 65536:
                    raise LaneError('SHARED_ASSET_BUDGET', 'The shared asset tree exceeds its bounded inventory.')
                path = Path(entry.path)
                if directory == folder and entry.name == '.cache':
                    continue
                reject_links(path, root)
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                elif entry.is_file(follow_symlinks=False):
                    observed.add(path.relative_to(folder).as_posix())
                else:
                    raise LaneError('SHARED_ASSET_INTEGRITY', 'An asset member is not a regular file or directory.')
    if observed != expected_paths:
        raise LaneError('SHARED_ASSET_INTEGRITY', 'The installed asset file set differs from its pinned manifest.')
    return folder, {**row, 'installation_manifest_sha256': expected,
                    'files_sha256': hashlib.sha256(canonical_json_bytes(files)).hexdigest()}
