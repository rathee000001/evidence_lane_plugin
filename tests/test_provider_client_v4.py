"""Resident provider protocol fixtures: identity, byte bounds and no replay."""
import json
from uuid import uuid4

import pytest
from evidence_lane_plugin import provider_client
from evidence_lane_plugin.errors import LaneError


@pytest.mark.parametrize('fault', [None, 'nonce', 'worker', 'operation', 'failure', 'timeout'])
def test_resident_reply_is_bound_to_the_exact_request(monkeypatch, fault):
    worker = str(uuid4())
    binding = {'worker_id': worker, 'address': r'\\.\pipe\EvidenceLaneProvider-v4-' + worker, 'authkey': 'ab' * 32}
    calls = []
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def send_bytes(self, payload):
            self.request = json.loads(payload)
        def poll(self, timeout):
            assert timeout == 30
            return fault != 'timeout'
        def recv_bytes(self, limit):
            assert limit == 4_194_304
            response = self.request | {'status': 'ok', 'result': {'fixture': True}}
            field = {'nonce': 'request_id', 'worker': 'worker_id', 'operation': 'operation', 'failure': 'status'}.get(fault)
            if field:
                response[field] = 'wrong'
            return json.dumps(response).encode()
    def connect(address, **kwargs):
        calls.append(address)
        assert address == binding['address'] and kwargs == {'family': 'AF_PIPE', 'authkey': bytes.fromhex(binding['authkey'])}
        return Connection()
    monkeypatch.setattr(provider_client, 'Client', connect)
    if fault:
        with pytest.raises(LaneError) as error:
            provider_client.call_provider_worker(binding, 'code_embed_text', {'texts': ['fixture']})
        assert error.value.code == 'PROVIDER_WORKER_FAILED'
    else:
        assert provider_client.call_provider_worker(binding, 'code_embed_text', {}) == {'fixture': True}
    assert len(calls) == 1


def test_remote_pipe_and_unbounded_payload_are_refused_before_connection(monkeypatch):
    binding = {'worker_id': str(uuid4()), 'address': r'\\remote\pipe\other', 'authkey': 'ab' * 32}
    monkeypatch.setattr(provider_client, 'Client', lambda *a, **k: pytest.fail('No connection permitted'))
    with pytest.raises(LaneError) as error:
        provider_client.call_provider_worker(binding, 'code_embed_text', {})
    assert error.value.code == 'PROVIDER_WORKER_BINDING'
    binding['address'] = r'\\.\pipe\EvidenceLaneProvider-v4-' + binding['worker_id']
    with pytest.raises(LaneError) as error:
        provider_client.call_provider_worker(binding, 'code_embed_text', {'texts': ['x' * 1_048_577]})
    assert error.value.code == 'PROVIDER_INPUT_BUDGET'


def test_authentication_failure_returns_a_bounded_error_without_retry(monkeypatch):
    binding = {'worker_id': str(uuid4()), 'authkey': 'ab' * 32}
    binding['address'] = r'\\.\pipe\EvidenceLaneProvider-v4-' + binding['worker_id']
    calls = []
    def denied(*args, **kwargs):
        calls.append(True)
        raise provider_client.AuthenticationError('fixture rejection')
    monkeypatch.setattr(provider_client, 'Client', denied)
    with pytest.raises(LaneError) as error:
        provider_client.call_provider_worker(binding, 'probe', {})
    assert error.value.code == 'PROVIDER_WORKER_FAILED' and calls == [True]
