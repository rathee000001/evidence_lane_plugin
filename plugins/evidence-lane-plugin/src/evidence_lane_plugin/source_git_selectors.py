"""Current Git checkpoint proof; working-copy bytes retain separate identities."""
from __future__ import annotations

import hashlib
import re

from .code_profile import _git_reference
from .code_workers import git_checkpoint
from .errors import LaneError


def observe_checkpoint(store, snapshot_id, path_resolver, max_files, observe=None):
    if snapshot_id is None:
        raise LaneError('SOURCE_SELECTOR_GIT_CHECKPOINT_REQUIRED', 'Select the exact current Sources Git checkpoint.')
    reference = _git_reference(store, snapshot_id)
    arguments = {'repository': str(path_resolver('.')), 'max_files': max_files}
    observed = (observe or git_checkpoint)(arguments)
    if (not reference['registered_worktree_clean'] or not observed['clean']
            or observed['head_commit_sha'] != reference['head_commit_sha']
            or observed['head_tree_sha'] != reference['head_tree_sha']):
        raise LaneError('SOURCE_SELECTOR_GIT_CHECKPOINT_CHANGED', 'The current clean Git commit and tree must match the selected Sources checkpoint.')
    return {'reference': reference, 'checkpoint': observed}


def matches_blob(content, oid):
    if not isinstance(oid, str) or not re.fullmatch(r'[0-9a-f]{40}(?:[0-9a-f]{24})?', oid):
        return False
    return hashlib.new('sha1' if len(oid) == 40 else 'sha256',
        b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest() == oid
