"""One recorded directory selection for classification, capture and refresh.

Reuses the admitted tracked/safe-untracked and bounded filesystem enumerator.
Older objects without a selection field retain their original capture policy.
This module owns no new database or business authority.
"""
from __future__ import annotations

import json

from .errors import LaneError
from .hashing import canonical_json_bytes, sha256_bytes

SELECTIONS = {'GIT_INDEX_AND_SAFE_UNTRACKED', 'BOUNDED_FILESYSTEM_FALLBACK'}


def capture_selected_directory(path, spec, policy, capture):
    from .source_authority import _stable_file_identity
    from .source_intake import _bounded_directory_members
    if policy or spec.directory_selection not in SELECTIONS:
        raise LaneError('SOURCE_SELECTION_POLICY', 'Use the recorded version-four directory selection without a conflicting policy.')
    selected, receipt = _bounded_directory_members(path, required_selection=spec.directory_selection, capture=capture)
    identity = sha256_bytes(canonical_json_bytes([{'path': name, 'bytes': size} for name, size in selected]))
    if spec.expected_directory_path_size_sha256 is not None and identity != spec.expected_directory_path_size_sha256:
        raise LaneError('SOURCE_SELECTION_CHANGED', 'Directory membership changed between classification and capture.')
    members = []
    for name, expected_size in selected:
        size, digest = _stable_file_identity(path / name, capture)
        if size != expected_size:
            raise LaneError('SOURCE_SELECTION_CHANGED', 'A selected file changed size during source capture.')
        members.append({'member_path': name, 'member_kind': 'file', 'size_bytes': size,
            'sha256': digest, 'policy_state': 'INCLUDED', 'policy_reason': 'POLICY_APPROVED'})
    after, after_receipt = _bounded_directory_members(path, required_selection=spec.directory_selection, capture=capture)
    if after != selected or after_receipt['excluded_class_counts'] != receipt['excluded_class_counts']:
        raise LaneError('SOURCE_SELECTION_CHANGED', 'Directory membership changed during source capture.')
    for member in members:
        if _stable_file_identity(path / member['member_path'], capture) != (member['size_bytes'], member['sha256']):
            raise LaneError('SOURCE_SELECTION_CHANGED', 'A selected source changed before its complete directory observation finished.')
    exclusions = tuple({'policy_reason': reason, 'excluded_entry_count': count,
        'excluded_bytes': None, 'descendant_members_enumerated': False, 'member_paths_stored': False}
        for reason, count in sorted(receipt['excluded_class_counts'].items()))
    return tuple(members), exclusions


def registered_directory_selection(store, object_id):
    from .source_authority import _source_store
    lane = _source_store(store)
    with lane.connection(read_only=True) as connection:
        row = connection.execute('SELECT CASE WHEN length(CAST(receipt_json AS BLOB))<=65536 THEN receipt_json END AS receipt_json,'
            'receipt_sha256 FROM source_policy_receipt WHERE object_id=?',
            (object_id,)).fetchone()
    if row is None or row['receipt_json'] is None:
        raise LaneError('SOURCE_SELECTION_POLICY', 'The registered source policy receipt is missing or exceeds its read bound.')
    try:
        body = json.loads(row['receipt_json'])
        if body['object_id'] != object_id or sha256_bytes(canonical_json_bytes(body)) != row['receipt_sha256']:
            raise ValueError('policy receipt identity')
        selection = body.get('directory_selection')
        if selection is not None and (selection not in SELECTIONS or body.get('directory_selection_version') != 4):
            raise ValueError('unsupported policy')
    except (TypeError, KeyError, ValueError) as error:
        raise LaneError('SOURCE_SELECTION_POLICY', 'The source selection receipt failed integrity or version validation.') from error
    return selection
