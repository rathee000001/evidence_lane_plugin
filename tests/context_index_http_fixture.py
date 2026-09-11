"""Controlled HTTPX transport for the four real REST adapter shapes."""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

HASH = 'a' * 64
SOURCE = 'b' * 64
RECORDS = [
    {'record_id': 'r1', 'vector': [0.1, 0.2, 0.3], 'text': 'first'},
    {'record_id': 'r2', 'vector': [0.3, 0.2, 0.1], 'text': 'second'},
]
ENDPOINT = 'https://index.example.test'
RESOURCES = {'Pinecone': 'Pinecone:namespace-one', 'Weaviate': 'Weaviate:EvidenceLane',
    'Milvus': 'Milvus:default/EvidenceLane', 'OpenSearch': 'OpenSearch:evidence-lane'}
BACKENDS = {'Pinecone': ('pinecone.data-plane-rest', '2025-10'),
    'Weaviate': ('weaviate.rest-graphql', 'v1'), 'Milvus': ('milvus.rest', 'v2'),
    'OpenSearch': ('opensearch.rest', 'v2')}
ENVIRONMENT = {
    'PINECONE_API_KEY': 'test-pinecone-key-0123456789', 'PINECONE_HOST': ENDPOINT,
    'WEAVIATE_API_KEY': 'test-weaviate-key-0123456789', 'WEAVIATE_URL': ENDPOINT,
    'MILVUS_TOKEN': 'test-milvus-token-0123456789', 'MILVUS_URI': ENDPOINT,
    'OPENSEARCH_USERNAME': 'test-user', 'OPENSEARCH_PASSWORD': 'test-password-0123456789',
    'OPENSEARCH_URL': ENDPOINT,
}
CONFIG_KEYS = {'Pinecone': ['PINECONE_API_KEY', 'PINECONE_HOST'],
    'Weaviate': ['WEAVIATE_API_KEY', 'WEAVIATE_URL'], 'Milvus': ['MILVUS_TOKEN', 'MILVUS_URI'],
    'OpenSearch': ['OPENSEARCH_PASSWORD', 'OPENSEARCH_URL', 'OPENSEARCH_USERNAME']}


class IndexTransport:
    def __init__(self, tool_id, operation, configuration=None):
        self.tool_id, self.operation = tool_id, operation
        self.configuration = configuration or {}
        self.requests = []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        if self.configuration.get('marker'):
            Path(self.configuration['marker']).write_text(str(len(self.requests)))
        if self.configuration.get('delay_seconds'):
            time.sleep(self.configuration['delay_seconds'])
        assert request.url.scheme == 'https' and request.url.host == 'index.example.test'
        raw = request.read()
        for value in ENVIRONMENT.values():
            if value != ENDPOINT:
                assert value.encode() not in raw
        if self.tool_id == 'Pinecone':
            assert request.headers['Api-Key'] == ENVIRONMENT['PINECONE_API_KEY']
            assert request.headers['X-Pinecone-API-Version'] == '2025-10'
            expected = {'query': '/query', 'upsert_changed': '/vectors/upsert', 'delete_identity': '/vectors/delete'}[self.operation]
        elif self.tool_id == 'Weaviate':
            assert request.headers['Authorization'] == 'Bearer ' + ENVIRONMENT['WEAVIATE_API_KEY']
            expected = {'query': '/v1/graphql', 'upsert_changed': '/v1/batch/objects',
                'delete_identity': '/v1/batch/objects'}[self.operation]
            assert request.method == ('DELETE' if self.operation == 'delete_identity' else 'POST')
        elif self.tool_id == 'Milvus':
            assert request.headers['Authorization'] == 'Bearer ' + ENVIRONMENT['MILVUS_TOKEN']
            expected = '/v2/vectordb/entities/' + {'query': 'search', 'upsert_changed': 'upsert',
                'delete_identity': 'delete'}[self.operation]
        else:
            decoded = base64_decode(request.headers['Authorization'])
            assert decoded == ENVIRONMENT['OPENSEARCH_USERNAME'] + ':' + ENVIRONMENT['OPENSEARCH_PASSWORD']
            expected = '/evidence-lane/' + {'query': '_search', 'upsert_changed': '_bulk',
                'delete_identity': '_delete_by_query'}[self.operation]
        assert request.url.path == expected and request.method in {'POST', 'DELETE'}
        if request.headers['Content-Type'] == 'application/x-ndjson':
            lines = raw.decode().splitlines()
            assert len(lines) == 4 and all(json.loads(line) for line in lines)
        else:
            body = json.loads(raw)
            assert body
            assert HASH in raw.decode()
        status = self.configuration.get('status', 200)
        response = self._response()
        if self.configuration.get('provider_failure'):
            response = self._failure()
        if self.configuration.get('reflect_secret'):
            response = {'matches': [{'id': ENVIRONMENT['PINECONE_API_KEY'], 'score': 1.0}]}
        content = json.dumps(response).encode()
        if self.configuration.get('oversize'):
            content += b' ' * 5000
        headers = {'Content-Type': 'application/json'}
        if self.configuration.get('content_length'):
            headers['Content-Length'] = str(len(content))
        if 300 <= status < 400:
            headers['Location'] = 'https://other.invalid/escape'
        return httpx.Response(status, headers=headers, content=content, request=request)

    def _response(self):
        if self.tool_id == 'Pinecone':
            return ({'matches': [{'id': 'r1', 'score': 0.9}]} if self.operation == 'query' else
                {'upsertedCount': 2} if self.operation == 'upsert_changed' else {})
        if self.tool_id == 'Weaviate':
            return ({'data': {'Get': {'EvidenceLane': [{'evidence_id': 'r1',
                '_additional': {'id': '00000000-0000-0000-0000-000000000001', 'certainty': 0.9, 'distance': 0.1}}]}}}
                if self.operation == 'query' else
                [{'result': {'status': 'SUCCESS'}}, {'result': {'status': 'SUCCESS'}}]
                if self.operation == 'upsert_changed' else {'results': {'matches': 2, 'successful': 2, 'failed': 0}})
        if self.tool_id == 'Milvus':
            return ({'code': 0, 'data': [[{'evidence_id': 'r1', 'distance': 0.9}]]}
                if self.operation == 'query' else {'code': 0, 'data': {'upsertCount': 2}}
                if self.operation == 'upsert_changed' else {'code': 0, 'data': {'deleteCount': 2}})
        return ({'timed_out': False, '_shards': {'failed': 0},
            'hits': {'hits': [{'_id': 'r1', '_score': 0.9, '_source': {'evidence_id': 'r1'}}]}}
            if self.operation == 'query' else {'errors': False, 'items': [
                {'index': {'status': 201}}, {'index': {'status': 200}}]}
            if self.operation == 'upsert_changed' else {'timed_out': False, 'failures': [], 'deleted': 2})

    def _failure(self):
        if self.tool_id == 'Pinecone':
            return {'upsertedCount': 1} if self.operation == 'upsert_changed' else {'matches': [{'id': 4, 'score': 'x'}]}
        if self.tool_id == 'Weaviate':
            return {'errors': [{'message': 'failure'}]} if self.operation == 'query' else (
                [{'result': {'status': 'FAILED'}}] if self.operation == 'upsert_changed'
                else {'results': {'successful': 1, 'failed': 1}})
        if self.tool_id == 'Milvus':
            return {'code': 5, 'message': 'failure'}
        return {'timed_out': True, '_shards': {'failed': 1}, 'hits': {'hits': []}} if self.operation == 'query' else (
            {'errors': True, 'items': []} if self.operation == 'upsert_changed'
            else {'timed_out': False, 'failures': [{'reason': 'failure'}], 'deleted': 1})


def base64_decode(value):
    import base64
    kind, encoded = value.split(' ', 1)
    assert kind == 'Basic'
    return base64.b64decode(encoded).decode()
