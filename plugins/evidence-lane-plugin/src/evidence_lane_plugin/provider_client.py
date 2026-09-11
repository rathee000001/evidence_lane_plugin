"""Authenticated bounded JSON for an engine-owned local provider worker."""
from __future__ import annotations

import json
import re
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client
from uuid import uuid4

from .errors import LaneError


def call_provider_worker(binding, operation, arguments, *, timeout=30):
    if (not re.fullmatch(r'\\\\\.\\pipe\\EvidenceLaneProvider-v4-[a-f0-9-]{36}', binding.get('address', ''))
            or not re.fullmatch(r'[a-f0-9]{64}', binding.get('authkey', ''))
            or operation not in {'probe', 'code_embed_text', 'rapidocr_lines'} or not 1 <= timeout <= 300):
        raise LaneError('PROVIDER_WORKER_BINDING', 'The owned provider connection is invalid.')
    nonce = str(uuid4())
    request = {'request_id': nonce, 'worker_id': binding['worker_id'], 'operation': operation, 'arguments': arguments}
    payload = json.dumps(request, allow_nan=False).encode()
    if len(payload) > 1_048_576:
        raise LaneError('PROVIDER_INPUT_BUDGET', 'The provider request exceeds its byte budget.')
    try:
        with Client(binding['address'], family='AF_PIPE', authkey=bytes.fromhex(binding['authkey'])) as connection:
            connection.send_bytes(payload)
            if not connection.poll(timeout):
                raise TimeoutError()
            response = json.loads(connection.recv_bytes(4_194_304))
        if (response['status'] != 'ok' or response['request_id'] != nonce
                or response['worker_id'] != binding['worker_id'] or response['operation'] != operation):
            raise ValueError()
        return response['result']
    except (OSError, ValueError, KeyError, TypeError, EOFError, TimeoutError, AuthenticationError):
        raise LaneError('PROVIDER_WORKER_FAILED', 'The provider did not return a bound result; no replay was issued.') from None
