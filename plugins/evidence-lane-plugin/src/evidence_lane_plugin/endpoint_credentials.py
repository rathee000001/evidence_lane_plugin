"""Current-user local endpoint credentials for Windows and the reduced Mac route."""
from __future__ import annotations

import json
import os
import stat

from .credentials import protect, unprotect
from .errors import LaneError
from .projects import atomic_json
from .storage import reject_links


def _posix_owner(path, info, *, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & (0o022 if directory else 0o077)
            or (not directory and info.st_nlink != 1)):
        raise LaneError('ENDPOINT_OWNER_PERMISSIONS', 'The local endpoint must be a private file owned by this OS user.')


def write_private_record(path, record, credential):
    reject_links(path, path.parent)
    if os.name == 'nt':
        protection, stored = 'windows-current-user-dpapi', protect(credential)
    elif os.name == 'posix':
        _posix_owner(path.parent, path.parent.stat(), directory=True)
        protection, stored = 'posix-current-user-file', credential
    else:
        raise LaneError('LOCAL_RUNTIME_UNSUPPORTED', 'This OS has no implemented private local endpoint.')
    # mkstemp in atomic_json creates the file with mode 0600 on POSIX. Existing
    # files are replaced atomically; no parent directory permissions are changed.
    atomic_json(path, {**record, 'protection': protection, 'credential': stored})


def read_private_record(path):
    reject_links(path, path.parent)
    if os.name == 'posix':
        _posix_owner(path.parent, path.parent.stat(), directory=True)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        if os.name == 'posix':
            _posix_owner(path, os.fstat(stream.fileno()))
        raw = stream.read(100_001)
    if len(raw) > 100_000:
        raise ValueError('Endpoint exceeds its bounded discovery budget')
    record = json.loads(raw)
    if os.name == 'nt' and record['protection'] == 'windows-current-user-dpapi':
        credential = unprotect(record['credential'])
    elif os.name == 'posix' and record['protection'] == 'posix-current-user-file':
        credential = record['credential']
    else:
        raise LaneError('ENDPOINT_PROTECTION_MISMATCH', 'The endpoint protection does not match this operating system.')
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
