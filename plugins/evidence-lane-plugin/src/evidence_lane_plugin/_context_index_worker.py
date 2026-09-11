"""Owned HTTPS adapters for rebuildable context indexes."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5


def _request_parts(request, operation: str, configuration: dict[str, str]):
    from evidence_lane_plugin.context_index_routing import _resource

    scope = _resource(request.tool_id, request.resource_id)
    if scope is None:
        raise ValueError('CONTEXT_INDEX_SCOPE_INVALID')
    identity = request.sqlite_identity_sha256
    headers = {'Accept': 'application/json'}
    auth = None
    content_type = 'application/json'
    body: object
    if request.tool_id == 'Pinecone':
        headers.update({'Api-Key': configuration['PINECONE_API_KEY'], 'X-Pinecone-API-Version': '2025-10'})
        if operation == 'query':
            path, body = '/query', {'namespace': scope['namespace'], 'vector': request.query_vector,
                'topK': request.top_k, 'includeValues': False, 'includeMetadata': False,
                'filter': {'sqlite_identity_sha256': {'$eq': identity}}}
        elif operation == 'upsert_changed':
            path, body = '/vectors/upsert', {'namespace': scope['namespace'], 'vectors': [
                {'id': row.record_id, 'values': row.vector, 'metadata': {'evidence_id': row.record_id,
                    'text': row.text, 'sqlite_identity_sha256': identity, 'source_sha256': request.source_sha256}}
                for row in request.records]}
        else:
            path, body = '/vectors/delete', {'namespace': scope['namespace'],
                'filter': {'sqlite_identity_sha256': {'$eq': identity}}}
    elif request.tool_id == 'Weaviate':
        headers['Authorization'] = 'Bearer ' + configuration['WEAVIATE_API_KEY']
        collection = scope['collection']
        if operation == 'query':
            vector = json.dumps(request.query_vector, separators=(',', ':'))
            operator = ('hybrid:{query:' + json.dumps(request.query_text) + ',vector:' + vector + ',alpha:0.5}'
                if request.query_text else 'nearVector:{vector:' + vector + '}')
            query = ('{Get{' + collection + '(' + operator + ',where:{path:["sqlite_identity_sha256"],operator:Equal,'
                'valueText:"' + identity + '"},limit:' + str(request.top_k) +
                '){evidence_id _additional{id distance certainty}}}}')
            path, body = '/v1/graphql', {'query': query}
        elif operation == 'upsert_changed':
            path, body = '/v1/batch/objects', [
                {'class': collection, 'id': str(uuid5(NAMESPACE_URL, identity + ':' + row.record_id)),
                    'properties': {'evidence_id': row.record_id, 'text': row.text,
                        'sqlite_identity_sha256': identity, 'source_sha256': request.source_sha256},
                    'vector': row.vector} for row in request.records]
        else:
            path, body = '/v1/batch/objects', {'match': {'class': collection,
                'where': {'path': ['sqlite_identity_sha256'], 'operator': 'Equal', 'valueText': identity}},
                'output': 'minimal'}
    elif request.tool_id == 'Milvus':
        headers.update({'Authorization': 'Bearer ' + configuration['MILVUS_TOKEN'], 'Request-Timeout': '5'})
        common = {'dbName': scope['database'], 'collectionName': scope['collection']}
        if operation == 'query':
            path, body = '/v2/vectordb/entities/search', {**common, 'data': [request.query_vector],
                'annsField': 'vector', 'filter': 'sqlite_identity_sha256 == "' + identity + '"',
                'limit': request.top_k, 'outputFields': ['evidence_id']}
        elif operation == 'upsert_changed':
            path, body = '/v2/vectordb/entities/upsert', {**common, 'data': [
                {'evidence_id': row.record_id, 'vector': row.vector, 'text': row.text,
                    'sqlite_identity_sha256': identity, 'source_sha256': request.source_sha256}
                for row in request.records]}
        else:
            path, body = '/v2/vectordb/entities/delete', {**common,
                'filter': 'sqlite_identity_sha256 == "' + identity + '"'}
    else:
        username, password = configuration['OPENSEARCH_USERNAME'], configuration['OPENSEARCH_PASSWORD']
        auth = (username, password)
        index = quote(scope['index'], safe='')
        if operation == 'query':
            knn = {'knn': {'vector': {'vector': request.query_vector, 'k': request.top_k}}}
            query = ({'bool': {'filter': [{'term': {'sqlite_identity_sha256.keyword': identity}}],
                'should': [knn, {'match': {'text': request.query_text}}], 'minimum_should_match': 1}}
                if request.query_text else {'bool': {'filter': [{'term': {'sqlite_identity_sha256.keyword': identity}}],
                    'must': [knn]}})
            path, body = '/' + index + '/_search', {'size': request.top_k, '_source': ['evidence_id'], 'query': query}
        elif operation == 'upsert_changed':
            lines: list[str] = []
            for row in request.records:
                lines.extend((json.dumps({'index': {'_id': row.record_id}}, separators=(',', ':')),
                    json.dumps({'evidence_id': row.record_id, 'vector': row.vector, 'text': row.text,
                        'sqlite_identity_sha256': identity, 'source_sha256': request.source_sha256}, separators=(',', ':'))))
            path, body, content_type = '/' + index + '/_bulk', ('\n'.join(lines) + '\n').encode(), 'application/x-ndjson'
        else:
            path, body = '/' + index + '/_delete_by_query', {'query': {'term': {'sqlite_identity_sha256.keyword': identity}}}
    content = body if isinstance(body, bytes) else json.dumps(body, separators=(',', ':'), ensure_ascii=True).encode()
    headers['Content-Type'] = content_type
    return ('DELETE' if request.tool_id == 'Weaviate' and operation == 'delete_identity' else 'POST'), path, headers, auth, content


def _bounded_rows(rows, limit):
    if not isinstance(rows, list) or len(rows) > limit:
        raise TypeError('CONTEXT_INDEX_PROVIDER_FAILURE')
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError('CONTEXT_INDEX_PROVIDER_FAILURE')
        record_id, score = row['record_id'], row['score']
        if (not isinstance(record_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', record_id)
                or type(score) not in {int, float} or not math.isfinite(score)):
            raise ValueError('CONTEXT_INDEX_PROVIDER_FAILURE')
        result.append({'record_id': record_id, 'score': float(score)})
    if len({row['record_id'] for row in result}) != len(result):
        raise ValueError('CONTEXT_INDEX_PROVIDER_FAILURE')
    return {'matches': result, 'match_count': len(result)}


def _provider_result(request, operation: str, response):
    if request.tool_id == 'Pinecone':
        if operation == 'query':
            return _bounded_rows([{'record_id': row['id'], 'score': row['score']} for row in response['matches']], request.top_k)
        if operation == 'upsert_changed':
            value = response.get('upsertedCount')
            if type(value) is not int or value != len(request.records):
                raise ValueError('CONTEXT_INDEX_PROVIDER_FAILURE')
            return {'upserted_count': value}
        if response != {}:
            raise ValueError('CONTEXT_INDEX_PROVIDER_FAILURE')
        return {'deleted_count': None, 'delete_count_known': False}
    if request.tool_id == 'Weaviate':
        collection = request.resource_id.split(':', 1)[1]
        if operation == 'query':
            if response.get('errors') or not isinstance(response.get('data', {}).get('Get', {}).get(collection), list):
                raise ValueError('CONTEXT_INDEX_PROVIDER_FAILURE')
            rows = []
            for item in response['data']['Get'][collection]:
                extra = item['_additional']
                score = extra.get('certainty')
                if score is None:
                    score = -float(extra['distance'])
                rows.append({'record_id': item['evidence_id'], 'score': score})
            return _bounded_rows(rows, request.top_k)
        if operation == 'upsert_changed':
            if (not isinstance(response, list) or len(response) != len(request.records)
                    or any(row.get('result', {}).get('status') != 'SUCCESS' or row.get('result', {}).get('errors') for row in response)):
                raise ValueError('CONTEXT_INDEX_PROVIDER_FAILURE')
            return {'upserted_count': len(response)}
        result = response.get('results')
        if not isinstance(result, dict) or result.get('failed') != 0 or type(result.get('successful')) is not int:
            raise ValueError('CONTEXT_INDEX_PROVIDER_FAILURE')
        return {'deleted_count': result['successful'], 'delete_count_known': True}
    if request.tool_id == 'Milvus':
        if response.get('code') != 0 or not isinstance(response.get('data'), (dict, list)):
            raise ValueError('CONTEXT_INDEX_PROVIDER_FAILURE')
        data = response['data']
        if operation == 'query':
            rows = data[0] if data and isinstance(data[0], list) else data
            return _bounded_rows([{'record_id': row.get('evidence_id', row.get('id')), 'score': row.get('distance')}
                for row in rows], request.top_k)
        key = 'upsertCount' if operation == 'upsert_changed' else 'deleteCount'
        value = data.get(key)
        if type(value) is not int or value < 0 or operation == 'upsert_changed' and value != len(request.records):
            raise ValueError('CONTEXT_INDEX_PROVIDER_FAILURE')
        return ({'upserted_count': value} if operation == 'upsert_changed'
            else {'deleted_count': value, 'delete_count_known': True})
    if operation == 'query':
        if response.get('timed_out') is not False or response.get('_shards', {}).get('failed') != 0:
            raise ValueError('CONTEXT_INDEX_PROVIDER_FAILURE')
        return _bounded_rows([{'record_id': row.get('_source', {}).get('evidence_id', row.get('_id')), 'score': row['_score']}
            for row in response.get('hits', {}).get('hits', [])], request.top_k)
    if operation == 'upsert_changed':
        items = response.get('items')
        if response.get('errors') is not False or not isinstance(items, list) or len(items) != len(request.records):
            raise ValueError('CONTEXT_INDEX_PROVIDER_FAILURE')
        if any(not isinstance(row.get('index', {}).get('status'), int) or not 200 <= row['index']['status'] < 300 for row in items):
            raise ValueError('CONTEXT_INDEX_PROVIDER_FAILURE')
        return {'upserted_count': len(items)}
    value = response.get('deleted')
    if response.get('timed_out') is not False or response.get('failures') or type(value) is not int or value < 0:
        raise ValueError('CONTEXT_INDEX_PROVIDER_FAILURE')
    return {'deleted_count': value, 'delete_count_known': True}


def execute(request, operation: str, project_id: str, endpoint: str, configuration: dict[str, str], expires_at: str):
    import httpx

    from evidence_lane_plugin.context_index_routing import _endpoint
    from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes

    if _endpoint(endpoint) != endpoint:
        raise ValueError('CONTEXT_INDEX_ENDPOINT_CHANGED')
    expiry = datetime.fromisoformat(expires_at) if expires_at != 'NO_EXPIRY' else None
    if expiry is not None and (expiry.tzinfo is None or datetime.now(UTC) >= expiry):
        raise ValueError('CONTEXT_INDEX_GRANT_EXPIRED')
    method, path, headers, auth, content = _request_parts(request, operation, configuration)
    if len(content) > 2_097_152:
        raise ValueError('CONTEXT_INDEX_REQUEST_BUDGET')
    deadline = request.timeout_seconds
    with (httpx.Client(base_url=endpoint, verify=True, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(min(5.0, deadline))) as client,
            client.stream(method, path, headers=headers, auth=auth, content=content) as response):
            if response.status_code != 200 or response.is_redirect:
                raise ValueError('CONTEXT_INDEX_HTTP_STATUS')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isascii() or not length.isdigit() or int(length) > request.max_response_bytes):
                raise ValueError('CONTEXT_INDEX_RESPONSE_BUDGET')
            raw = bytearray()
            for chunk in response.iter_bytes(65_536):
                if len(raw) + len(chunk) > request.max_response_bytes:
                    raise ValueError('CONTEXT_INDEX_RESPONSE_BUDGET')
                raw.extend(chunk)
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeError):
                raise ValueError('CONTEXT_INDEX_RESPONSE_INVALID') from None
            provider = _provider_result(request, operation, value)
            observation = {'method': method, 'path_sha256': sha256_bytes(path.encode()).lower(),
                'request_bytes': len(content), 'request_sha256': sha256_bytes(content).lower(),
                'response_bytes': len(raw), 'response_sha256': sha256_bytes(bytes(raw)).lower(),
                'status_code': response.status_code, 'endpoint_sha256': sha256_bytes(endpoint.encode()).lower()}
    result = {'schema': 'evidence-lane.context-index-execution.v4', 'status': 'PASS',
        'tool_id': request.tool_id, 'operation': operation, 'project_id': project_id,
        'lane_id': request.lane_id, 'authority_id': request.authority_id, 'resource_id': request.resource_id,
        'sqlite_identity_sha256': request.sqlite_identity_sha256, 'source_sha256': request.source_sha256,
        'provider_result': provider, 'request_observation': observation,
        'sqlite_remains_authority': True, 'remote_index_rebuildable': True, 'remote_observation_atomic': False}
    encoded = canonical_json_bytes(result)
    if any(value.encode() in encoded for value in configuration.values()):
        raise ValueError('CONTEXT_INDEX_RESPONSE_INVALID')
    return result


def main():
    with (Path.cwd() / 'request.json').open('rb') as stream:
        raw = stream.read(2_097_153)
    if len(sys.argv) != 2 or len(raw) > 2_097_152 or hashlib.sha256(raw).hexdigest() != sys.argv[1]:
        raise ValueError('CONTEXT_INDEX_REQUEST_BINDING_INVALID')
    body = json.loads(raw)
    if set(body) != {'schema', 'operation', 'arguments', 'project_id', 'expires_at', 'programs'}:
        raise ValueError('CONTEXT_INDEX_REQUEST_INVALID')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from evidence_lane_plugin.context_index_routing import (
        ContextIndexQueryRequest,
        ContextIndexSyncRequest,
        _adapter_programs,
    )
    from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes

    programs = _adapter_programs()
    if body['schema'] != 'evidence-lane.context-index-request.v4' or body['programs'] != programs:
        raise ValueError('CONTEXT_INDEX_PROGRAM_BINDING_INVALID')
    model = ContextIndexQueryRequest if body['operation'] == 'query' else ContextIndexSyncRequest
    request = model.model_validate(body['arguments'])
    endpoint = os.environ.pop('_EVI_INDEX_ENDPOINT', '')
    configuration = json.loads(os.environ.pop('_EVI_INDEX_CONFIG', ''))
    result = execute(request, body['operation'], body['project_id'], endpoint, configuration, body['expires_at'])
    print(json.dumps({'status': 'ok', 'request_sha256': sys.argv[1],
        'program_sha256': sha256_bytes(canonical_json_bytes(programs)).lower(), 'result': result}, separators=(',', ':')))


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    try:
        main()
    except Exception as error:  # noqa: BLE001 - keep provider responses and credentials inside the worker
        code = str(error)
        if code not in {'CONTEXT_INDEX_HTTP_STATUS', 'CONTEXT_INDEX_RESPONSE_BUDGET',
                'CONTEXT_INDEX_PROVIDER_FAILURE', 'CONTEXT_INDEX_GRANT_EXPIRED',
                'CONTEXT_INDEX_ENDPOINT_CHANGED', 'CONTEXT_INDEX_RESPONSE_INVALID'}:
            code = 'CONTEXT_INDEX_WORKER_FAILED'
        print(json.dumps({'status': 'error', 'error_code': code}))
        raise SystemExit(1) from None
