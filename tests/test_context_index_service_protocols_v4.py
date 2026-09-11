"""Provider-specific protocol and result checks through the actual HTTPX client."""
from __future__ import annotations

import httpx
import pytest
from evidence_lane_plugin._context_index_worker import execute
from evidence_lane_plugin.context_index_routing import (
    ContextIndexQueryRequest,
    ContextIndexSyncRequest,
    _resource,
)

from .context_index_http_fixture import (
    ENDPOINT,
    ENVIRONMENT,
    HASH,
    RECORDS,
    RESOURCES,
    SOURCE,
    IndexTransport,
)


def request(tool_id, operation):
    common = {'tool_id': tool_id, 'lane_id': 'memory', 'authority_id': 'memory',
        'resource_id': RESOURCES[tool_id], 'sqlite_identity_sha256': HASH, 'source_sha256': SOURCE}
    return (ContextIndexQueryRequest(**common, query_vector=[0.2, 0.3, 0.4], query_text='evidence', top_k=5)
        if operation == 'query' else ContextIndexSyncRequest(**common, operation=operation,
            records=RECORDS if operation == 'upsert_changed' else []))


def configuration(tool_id):
    keys = {'Pinecone': ('PINECONE_API_KEY',), 'Weaviate': ('WEAVIATE_API_KEY',),
        'Milvus': ('MILVUS_TOKEN',), 'OpenSearch': ('OPENSEARCH_PASSWORD', 'OPENSEARCH_USERNAME')}[tool_id]
    return {key: ENVIRONMENT[key] for key in keys}


def run(monkeypatch, tool_id, operation, options=None, **changes):
    transport = IndexTransport(tool_id, operation, options)
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs))
    value = execute(request(tool_id, operation).model_copy(update=changes), operation, 'project-one', ENDPOINT,
        configuration(tool_id), 'NO_EXPIRY')
    return value, transport


@pytest.mark.parametrize('tool_id', ['Pinecone', 'Weaviate', 'Milvus', 'OpenSearch'])
@pytest.mark.parametrize('operation', ['query', 'upsert_changed', 'delete_identity'])
def test_each_real_http_client_shape_executes_and_parses_exact_success(monkeypatch, tool_id, operation):
    result, transport = run(monkeypatch, tool_id, operation)
    assert len(transport.requests) == 1 and result['tool_id'] == tool_id and result['operation'] == operation
    assert result['project_id'] == 'project-one' and result['sqlite_identity_sha256'] == HASH
    assert result['sqlite_remains_authority'] and result['remote_index_rebuildable'] and not result['remote_observation_atomic']
    provider = result['provider_result']
    if operation == 'query':
        assert provider == {'matches': [{'record_id': 'r1', 'score': 0.9}], 'match_count': 1}
    elif operation == 'upsert_changed':
        assert provider == {'upserted_count': 2}
    else:
        assert provider['deleted_count'] in {None, 2}


@pytest.mark.parametrize('tool_id', ['Pinecone', 'Weaviate', 'Milvus', 'OpenSearch'])
@pytest.mark.parametrize('operation', ['query', 'upsert_changed', 'delete_identity'])
def test_provider_partial_or_error_body_is_not_admitted(monkeypatch, tool_id, operation):
    with pytest.raises(ValueError, match='CONTEXT_INDEX_PROVIDER_FAILURE'):
        run(monkeypatch, tool_id, operation, {'provider_failure': True})


@pytest.mark.parametrize(('options', 'code'), [({'status': 302}, 'CONTEXT_INDEX_HTTP_STATUS'),
    ({'status': 401}, 'CONTEXT_INDEX_HTTP_STATUS'), ({'oversize': True}, 'CONTEXT_INDEX_RESPONSE_BUDGET'),
    ({'oversize': True, 'content_length': True}, 'CONTEXT_INDEX_RESPONSE_BUDGET')])
def test_http_status_redirect_and_response_bounds(monkeypatch, options, code):
    with pytest.raises(ValueError, match=code):
        run(monkeypatch, 'Pinecone', 'query', options, max_response_bytes=1024)


def test_provider_secret_reflection_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match='CONTEXT_INDEX_RESPONSE_INVALID'):
        run(monkeypatch, 'Pinecone', 'query', {'reflect_secret': True})


@pytest.mark.parametrize(('tool_id', 'value'), [
    ('Pinecone', 'Pinecone:namespace-one'), ('Weaviate', 'Weaviate:EvidenceLane'),
    ('Milvus', 'Milvus:default/EvidenceLane'), ('OpenSearch', 'OpenSearch:evidence-lane')])
def test_resource_parser_accepts_only_the_provider_exact_scope(tool_id, value):
    assert _resource(tool_id, value)
    for invalid in ('other:' + value.split(':', 1)[1], value + '/../escape', value + '/extra'):
        assert _resource(tool_id, invalid) is None


@pytest.mark.parametrize('changes', [
    {'tool_id': 'Other'}, {'lane_id': 'discussion'}, {'resource_id': 'Pinecone:../escape'},
    {'sqlite_identity_sha256': 'A' * 64}, {'source_sha256': 'x'}, {'timeout_seconds': True},
    {'timeout_seconds': float('nan')}, {'max_response_bytes': 1023},
])
def test_requests_are_strict_and_bound(changes):
    values = {'tool_id': 'Pinecone', 'lane_id': 'memory', 'authority_id': 'memory',
        'resource_id': RESOURCES['Pinecone'], 'sqlite_identity_sha256': HASH, 'source_sha256': SOURCE,
        'query_vector': [1.0]} | changes
    with pytest.raises(ValueError):
        ContextIndexQueryRequest(**values)
