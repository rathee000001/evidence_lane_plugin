"""Current-user local endpoint credentials for persistent Windows Desktop hosts."""
from __future__ import annotations

import json
import os

from .credentials import protect, unprotect
from .errors import LaneError
from .projects import atomic_json
from .storage import reject_links


def write_private_record(path, record, credential):
    reject_links(path, path.parent)
    if os.name == 'nt':
        protection, stored = 'windows-current-user-dpapi', protect(credential)
    else:
        raise LaneError('WINDOWS_HOST_REQUIRED', 'Evidence Lane supports persistent local Windows Codex Desktop hosts.')
    atomic_json(path, {**record, 'protection': protection, 'credential': stored})


def read_private_record(path):
    reject_links(path, path.parent)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        raw = stream.read(100_001)
    if len(raw) > 100_000:
        raise ValueError('Endpoint exceeds its bounded discovery budget')
    record = json.loads(raw)
    if os.name == 'nt' and record['protection'] == 'windows-current-user-dpapi':
        credential = unprotect(record['credential'])
    else:
        raise LaneError('WINDOWS_HOST_REQUIRED', 'Evidence Lane supports persistent local Windows Codex Desktop hosts.')
    if not isinstance(credential, str) or not 32 <= len(credential) <= 256:
        raise ValueError('Invalid bounded endpoint credential')
    return record, credential


def write_endpoint(path, record, credential):
    write_private_record(path, record, credential)


def read_endpoint(path):
    record, credential = read_private_record(path)
    if (record['protocol_version'] != 4 or type(record['port']) is not int
            or not 0 < record['port'] < 65536 or not isinstance(record['instance_id'], str)):
        raise ValueError('Invalid endpoint identity')
    return record, credential
