"""Explicit operator declarations for isolated local storage fixtures only."""
from __future__ import annotations

import json


def declare_local_storage(runtime, state_root, storage_class='persistent_operator_declared'):
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / 'storage-policy.json').write_text(json.dumps({'format_version': 1, 'declarations': [{
        'state_root': str(state_root), 'volume_id': 'isolated-test-volume', 'storage_class': storage_class}]}), encoding='utf-8')
