"""Conditional context-index routing that never replaces SQLite authority."""

from __future__ import annotations

import importlib.metadata
import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, JsonValue, model_validator

from .errors import LaneError
from .hashing import canonical_json_bytes, sha256_bytes
from .registry import Contract

CONTEXT_INDEX_ROUTING_SCHEMA = "evidence-lane.context-index-routing.v1"

_SHA256 = re.compile(r"[A-F0-9]{64}")
_INDEXES: dict[str, dict[str, Any]] = {
    "SQLite_FTS5_BM25": {
        "kind": "local_authority_index",
        "credential_names": [],
        "transports": ["sqlite"],
    },
    "FAISS_CPU": {
        "kind": "local_rebuildable_vector_index",
        "credential_names": [],
        "transports": ["python"],
    },
    "Pinecone": {
        "kind": "remote_rebuildable_vector_index",
        "credential_names": ["PINECONE_API_KEY", "PINECONE_HOST"],
        "transports": ["https"],
    },
    "Weaviate": {
        "kind": "remote_rebuildable_hybrid_index",
        "credential_names": ["WEAVIATE_URL", "WEAVIATE_API_KEY"],
        "transports": ["https"],
    },
    "Milvus": {
        "kind": "remote_rebuildable_vector_index",
        "credential_names": ["MILVUS_URI", "MILVUS_TOKEN"],
        "transports": ["https", "grpc_adapter"],
    },
    "OpenSearch": {
        "kind": "remote_rebuildable_hybrid_index",
        "credential_names": [
            "OPENSEARCH_URL",
            "OPENSEARCH_USERNAME",
            "OPENSEARCH_PASSWORD",
        ],
        "transports": ["https"],
    },
}

CONTEXT_INDEX_QUERY = 'context_index_query'
CONTEXT_INDEX_SYNC = 'context_index_sync'
REMOTE_INDEX_TOOLS = ('Pinecone', 'Weaviate', 'Milvus', 'OpenSearch')
REMOTE_INDEX_BACKENDS = {
    'Pinecone': {'backend_id': 'pinecone.data-plane-rest', 'version': '2025-10',
        'config': ('PINECONE_API_KEY', 'PINECONE_HOST')},
    'Weaviate': {'backend_id': 'weaviate.rest-graphql', 'version': 'v1',
        'config': ('WEAVIATE_API_KEY', 'WEAVIATE_URL')},
    'Milvus': {'backend_id': 'milvus.rest', 'version': 'v2',
        'config': ('MILVUS_TOKEN', 'MILVUS_URI')},
    'OpenSearch': {'backend_id': 'opensearch.rest', 'version': 'v2',
        'config': ('OPENSEARCH_PASSWORD', 'OPENSEARCH_URL', 'OPENSEARCH_USERNAME')},
}
_LOWER_SHA256 = re.compile(r'[0-9a-f]{64}')
_IDENTIFIER = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,127}')
_RECORD_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')


class ContextIndexRecord(Contract):
    record_id: str = Field(min_length=1, max_length=128)
    vector: list[float] = Field(min_length=1, max_length=4096)
    text: str = Field(default='', max_length=32_768)

    @model_validator(mode='after')
    def finite_record(self):
        if (_RECORD_ID.fullmatch(self.record_id) is None
                or any(not math.isfinite(value) or abs(value) > 1e18 for value in self.vector)
                or not 1e-30 <= sum(value * value for value in self.vector) <= 1e38):
            raise ValueError('CONTEXT_INDEX_RECORD_INVALID')
        return self


class ContextIndexBaseRequest(Contract):
    tool_id: Literal['Pinecone', 'Weaviate', 'Milvus', 'OpenSearch']
    lane_id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    authority_id: str = Field(min_length=1, max_length=128)
    resource_id: str = Field(min_length=3, max_length=384)
    sqlite_identity_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    plugin_id: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9-]{2,63}$')
    timeout_seconds: float = Field(default=20, gt=0, le=60, allow_inf_nan=False)
    max_response_bytes: int = Field(default=2_097_152, ge=1024, le=8_388_608)

    @model_validator(mode='after')
    def exact_scope(self):
        from .lanes import CANONICAL_LANE_IDS
        if self.lane_id not in CANONICAL_LANE_IDS or not _resource(self.tool_id, self.resource_id):
            raise ValueError('CONTEXT_INDEX_SCOPE_INVALID')
        if not self.authority_id.strip() or any(ord(value) < 32 for value in self.authority_id):
            raise ValueError('CONTEXT_INDEX_AUTHORITY_INVALID')
        return self


class ContextIndexQueryRequest(ContextIndexBaseRequest):
    query_vector: list[float] = Field(min_length=1, max_length=4096)
    query_text: str | None = Field(default=None, max_length=4096)
    top_k: int = Field(default=20, ge=1, le=100)

    @model_validator(mode='after')
    def finite_query(self):
        if (any(not math.isfinite(value) or abs(value) > 1e18 for value in self.query_vector)
                or not 1e-30 <= sum(value * value for value in self.query_vector) <= 1e38
                or self.query_text is not None and not self.query_text.strip()):
            raise ValueError('CONTEXT_INDEX_QUERY_INVALID')
        return self


class ContextIndexSyncRequest(ContextIndexBaseRequest):
    operation: Literal['upsert_changed', 'delete_identity']
    records: list[ContextIndexRecord] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def exact_operation(self):
        if ((self.operation == 'upsert_changed' and not self.records)
                or (self.operation == 'delete_identity' and self.records)
                or len({row.record_id for row in self.records}) != len(self.records)
                or self.records and len({len(row.vector) for row in self.records}) != 1
                or len(canonical_json_bytes(self.model_dump(mode='json'))) > 2_097_152):
            raise ValueError('CONTEXT_INDEX_SYNC_INVALID')
        return self


class ContextIndexExecutionResult(Contract):
    tool_id: str
    operation: str
    result: dict[str, JsonValue]
    receipt_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


def _resource(tool_id: str, value: str) -> dict[str, str] | None:
    try:
        prefix, scope = value.split(':', 1)
        if prefix.casefold() != tool_id.casefold() or not scope or '\\' in scope or '..' in scope:
            return None
        if tool_id == 'Pinecone' and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}', scope):
            return {'namespace': scope}
        if tool_id == 'Weaviate' and _IDENTIFIER.fullmatch(scope) and scope[0].isupper():
            return {'collection': scope}
        if tool_id == 'Milvus':
            database, collection = scope.split('/', 1)
            if _IDENTIFIER.fullmatch(database) and _IDENTIFIER.fullmatch(collection):
                return {'database': database, 'collection': collection}
        if tool_id == 'OpenSearch' and re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', scope) and not scope.startswith(('_', '-', '+')):
            return {'index': scope}
    except ValueError:
        pass
    return None


def _endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {'', '/'} or parsed.port not in {None, 443}
            or ':' in parsed.hostname or len(value) > 2048):
        raise LaneError('CONTEXT_INDEX_ENDPOINT_INVALID', 'Configure one credential-free HTTPS service origin.')
    return f'https://{parsed.hostname}' + (f':{parsed.port}' if parsed.port else '')


def _configuration(tool_id: str, registration: dict) -> tuple[str, dict[str, str]]:
    expected = tuple(sorted(REMOTE_INDEX_BACKENDS[tool_id]['config']))
    keys = tuple(sorted(registration.get('config_env_keys', [])))
    if keys != expected:
        raise LaneError('CONTEXT_INDEX_CONFIGURATION_REQUIRED', 'Configure the exact environment references required by this service adapter.')
    values = {key: os.environ.get(key, '') for key in keys}
    if any(not value or len(value) > 4096 or any(ord(character) < 32 for character in value) for value in values.values()):
        raise LaneError('CONTEXT_INDEX_CONFIGURATION_UNAVAILABLE', 'A configured service value is unavailable or invalid.')
    endpoint_key = {'Pinecone': 'PINECONE_HOST', 'Weaviate': 'WEAVIATE_URL',
        'Milvus': 'MILVUS_URI', 'OpenSearch': 'OPENSEARCH_URL'}[tool_id]
    endpoint = _endpoint(values.pop(endpoint_key))
    return endpoint, values


def context_index_readiness(tool_id: str):
    def readiness(context, registration):
        if importlib.metadata.version('httpx') != '0.28.1':
            return {'ready': False, 'backend_version': REMOTE_INDEX_BACKENDS[tool_id]['version']}
        endpoint, values = _configuration(tool_id, registration)
        import httpx
        client = httpx.Client(base_url=endpoint, verify=True, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(5))
        client.close()
        return {'ready': bool(values), 'backend_version': REMOTE_INDEX_BACKENDS[tool_id]['version'],
            'basis': 'exact_config_references_and_httpx_constructor', 'service_contacted': False}
    return readiness


def _adapter_programs() -> dict[str, str]:
    from .hashing import sha256_file
    return {name: sha256_file(Path(__file__).with_name(name)).lower() for name in (
        'context_index_routing.py', '_context_index_worker.py', 'bounded_io.py', '_bounded_process_child.py')}


def _validate_execution(value: dict, request: ContextIndexBaseRequest, operation: str) -> None:
    try:
        if (not isinstance(value, dict) or set(value) != {'schema', 'status', 'tool_id', 'operation', 'project_id',
                'lane_id', 'authority_id', 'resource_id', 'sqlite_identity_sha256', 'source_sha256', 'provider_result',
                'request_observation', 'sqlite_remains_authority', 'remote_index_rebuildable', 'remote_observation_atomic'}
                or value['schema'] != 'evidence-lane.context-index-execution.v4' or value['status'] != 'PASS'
                or value['tool_id'] != request.tool_id or value['operation'] != operation
                or value['lane_id'] != request.lane_id or value['authority_id'] != request.authority_id
                or value['resource_id'] != request.resource_id
                or value['sqlite_identity_sha256'] != request.sqlite_identity_sha256
                or value['source_sha256'] != request.source_sha256
                or value['sqlite_remains_authority'] is not True or value['remote_index_rebuildable'] is not True
                or value['remote_observation_atomic'] is not False):
            raise ValueError()
        observation = value['request_observation']
        if (set(observation) != {'method', 'path_sha256', 'request_bytes', 'request_sha256', 'response_bytes',
                'response_sha256', 'status_code', 'endpoint_sha256'} or observation['method'] not in {'POST', 'DELETE'}
                or observation['status_code'] != 200 or any(type(observation[name]) is not int or observation[name] < 0
                    for name in ('request_bytes', 'response_bytes'))
                or observation['response_bytes'] > request.max_response_bytes
                or any(not isinstance(observation[name], str) or _LOWER_SHA256.fullmatch(observation[name]) is None
                    for name in ('path_sha256', 'request_sha256', 'response_sha256', 'endpoint_sha256'))):
            raise ValueError()
        result = value['provider_result']
        if not isinstance(result, dict):
            raise TypeError()
        if operation == 'query':
            rows = result.get('matches')
            if (set(result) != {'matches', 'match_count'} or not isinstance(rows, list)
                    or len(rows) != result['match_count'] or len(rows) > request.top_k):
                raise ValueError()
            for row in rows:
                if (set(row) != {'record_id', 'score'} or _RECORD_ID.fullmatch(row['record_id']) is None
                        or type(row['score']) not in {int, float} or not math.isfinite(row['score'])):
                    raise ValueError()
            if len({row['record_id'] for row in rows}) != len(rows):
                raise ValueError()
        elif operation == 'upsert_changed':
            if set(result) != {'upserted_count'} or result['upserted_count'] != len(request.records):
                raise ValueError()
        elif (set(result) != {'deleted_count', 'delete_count_known'} or type(result['delete_count_known']) is not bool
                or result['deleted_count'] is not None and (type(result['deleted_count']) is not int or result['deleted_count'] < 0)):
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise LaneError('CONTEXT_INDEX_RESULT_INVALID', 'The service adapter returned an invalid complete result.') from None


def execute_context_index(request: ContextIndexBaseRequest, *, engine, context, operation: str) -> ContextIndexExecutionResult:
    from .bounded_io import run_owned_bounded_process
    from .connector_governance import connector_service
    from .tool_routes import routes_for

    if context.execution is None or context.execution.guard is None:
        raise LaneError('DELTA_REQUIRED', 'Context-index execution requires the current project Delta.')
    spec = engine.registry.get(CONTEXT_INDEX_QUERY if operation == 'query' else CONTEXT_INDEX_SYNC)
    route = next(row for row in routes_for(spec) if row.argument_values[0][1] == (request.tool_id,))
    admission = context.tool_admission
    if not isinstance(admission, dict) or not isinstance(admission.get('extension'), dict):
        raise LaneError('CONTEXT_INDEX_ADMISSION_REQUIRED', 'Use the exact admitted service connector operation.')
    proof = engine.registry.tool_router.extensions.authorize(spec, route, context, request, admission['extension'])
    service = connector_service(engine, context.execution.store)
    registration = next((row for row in service.active_catalog() if row['plugin_id'] == proof['plugin_id']
        and row['version'] == proof['registration_version'] and row['digest'] == proof['registration_digest']), None)
    if registration is None:
        raise LaneError('PLUGIN_VERSION_CONFLICT', 'The admitted service registration changed.')
    endpoint, configuration = _configuration(request.tool_id, registration)
    programs = _adapter_programs()
    body = {'schema': 'evidence-lane.context-index-request.v4', 'operation': operation,
        'arguments': request.model_dump(mode='json'), 'project_id': context.project_id,
        'expires_at': proof['expires_at'], 'programs': programs}
    encoded = canonical_json_bytes(body)
    if len(encoded) > 2_097_152:
        raise LaneError('CONTEXT_INDEX_REQUEST_BUDGET', 'The context-index request exceeds its private descriptor bound.')
    request_sha = sha256_bytes(encoded).lower()
    program_sha = sha256_bytes(canonical_json_bytes(programs)).lower()
    secrets = [value.encode() for value in configuration.values()]
    with tempfile.TemporaryDirectory(prefix='evidence-lane-context-index-') as directory:
        (Path(directory) / 'request.json').write_bytes(encoded)
        environment = {name: value for name, value in os.environ.items() if name.upper() in {
            'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'}}
        environment.update(TEMP=directory, TMP=directory, _EVI_INDEX_ENDPOINT=endpoint,
            _EVI_INDEX_CONFIG=json.dumps(configuration, separators=(',', ':')))
        process = run_owned_bounded_process([sys.executable, '-I', '-B',
            str(Path(__file__).with_name('_context_index_worker.py')), request_sha], cwd=directory, env=environment,
            timeout_seconds=request.timeout_seconds, max_stdout_bytes=request.max_response_bytes + 262_144,
            max_stderr_bytes=65_536, check=context.execution.check)
        context.execution.check()
        try:
            response = json.loads(process.stdout)
            if process.returncode != 0:
                code = response.get('error_code')
                if code not in {'CONTEXT_INDEX_HTTP_STATUS', 'CONTEXT_INDEX_RESPONSE_BUDGET',
                        'CONTEXT_INDEX_PROVIDER_FAILURE', 'CONTEXT_INDEX_GRANT_EXPIRED',
                        'CONTEXT_INDEX_ENDPOINT_CHANGED', 'CONTEXT_INDEX_RESPONSE_INVALID'}:
                    code = 'CONTEXT_INDEX_WORKER_FAILED'
                raise LaneError(code, 'The context-index service operation failed without an admitted result.')
            if (response['status'] != 'ok' or response['request_sha256'] != request_sha
                    or response['program_sha256'] != program_sha or _adapter_programs() != programs):
                raise LaneError('CONTEXT_INDEX_WORKER_BINDING_CHANGED', 'The service worker request or program identity changed.')
            value = response['result']
            raw = canonical_json_bytes(value)
            if len(raw) > request.max_response_bytes or any(secret and secret in raw for secret in secrets):
                raise LaneError('CONTEXT_INDEX_RESULT_SECRET_OR_BUDGET', 'The context-index result violated its output boundary.')
            _validate_execution(value, request, operation)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise LaneError('CONTEXT_INDEX_RESULT_INVALID', 'The service worker returned an invalid complete result.') from None
    result = {**value, 'grant': proof, 'request_sha256': request_sha, 'program_sha256': program_sha,
        'owned_worker_joined': True, 'credential_values_persisted': False, 'automatic_retry': False}
    return ContextIndexExecutionResult(tool_id=request.tool_id, operation=operation, result=result,
        receipt_sha256=sha256_bytes(canonical_json_bytes(result)).lower())


def verify_context_index(context, request, output):
    valid = (output.tool_id == request.tool_id and output.receipt_sha256 == sha256_bytes(canonical_json_bytes(output.result)).lower()
        and output.result.get('owned_worker_joined') is True and output.result.get('sqlite_remains_authority') is True)
    return [{'check_id': name, 'passed': valid, 'evidence': {'tool_id': output.tool_id,
        'operation': output.operation, 'receipt_sha256': output.receipt_sha256}} for name in context.requested_checks]


def _register_remote_context_index_actions(engine):
    from .extension_routes import ExtensionBinding
    from .registry import ActionSpec
    from .tool_routes import ToolRoute

    def query(context, request):
        return execute_context_index(request, engine=engine, context=context, operation='query')
    def sync(context, request):
        return execute_context_index(request, engine=engine, context=context, operation=request.operation)
    role = (('tool_id', 'text'), ('operation', 'text'), ('result', 'json'), ('receipt_sha256', 'blob_hash'))
    def routes(handler):
        values = []
        for tool_id in REMOTE_INDEX_TOOLS:
            backend = REMOTE_INDEX_BACKENDS[tool_id]
            binding = ExtensionBinding(backend['backend_id'], backend['version'], 'python', 'context_index',
                'selected_by_action', 'context_index_result', role, context_index_readiness(tool_id),
                resource_fields=('resource_id',), plugin_id_field='plugin_id', lane_field='lane_id')
            values.append(ToolRoute('context_index.' + tool_id.casefold(), handler, ('Python', 'HTTPX', tool_id),
                argument_values=(('tool_id', (tool_id,)),), extension=binding))
        return tuple(values)
    engine.registry.register(ActionSpec(CONTEXT_INDEX_QUERY,
        'Query one exact rebuildable remote context index while retaining SQLite and source-hash authority.',
        ContextIndexQueryRequest, ContextIndexExecutionResult, query, permission='tools', profile='memory',
        workflow='manage-project-memory', requires_delta=True, required_tools=('Python', 'HTTPX'),
        verifier=verify_context_index, verification_checks=('context_index_result_binding',), tool_routes=routes(query)))
    engine.registry.register(ActionSpec(CONTEXT_INDEX_SYNC,
        'Upsert changed records or delete one SQLite identity from an exact rebuildable remote context index.',
        ContextIndexSyncRequest, ContextIndexExecutionResult, sync, permission='publish', mutates=True,
        profile='memory', workflow='manage-project-memory', requires_delta=True, required_tools=('Python', 'HTTPX'),
        verifier=verify_context_index, verification_checks=('context_index_result_binding',), tool_routes=routes(sync)))


def build_context_index_operation(
    *,
    tool_id: str,
    operation: str,
    project_id: str,
    authority_id: str,
    lane_id: str,
    sqlite_identity_sha256: str,
    source_sha256: str,
    granted_tools: Iterable[str],
    top_k: int = 20,
) -> dict[str, Any]:
    if tool_id not in _INDEXES:
        raise ValueError(f"Unknown context index: {tool_id}")
    exact_operation = operation.strip().lower()
    if exact_operation not in {"query", "upsert_changed", "delete_identity"}:
        raise ValueError("Context index operation is not supported.")
    for label, value in (
        ("sqlite_identity_sha256", sqlite_identity_sha256),
        ("source_sha256", source_sha256),
    ):
        if _SHA256.fullmatch(value.strip().upper()) is None:
            raise ValueError(f"{label} must be an exact SHA-256.")
    if not project_id.strip() or not authority_id.strip() or not lane_id.strip():
        raise ValueError("Context routing requires project, authority, and lane identity.")
    if not 1 <= int(top_k) <= 200:
        raise ValueError("Context index top_k must be between 1 and 200.")
    granted = set(granted_tools)
    remote = str(_INDEXES[tool_id]["kind"]).startswith("remote_")
    if remote and tool_id not in granted:
        status = "BLOCKED_PROJECT_GRANT_REQUIRED"
    else:
        status = "PASS"
    body = {
        "schema": CONTEXT_INDEX_ROUTING_SCHEMA,
        "status": status,
        "tool_id": tool_id,
        "index_kind": _INDEXES[tool_id]["kind"],
        "operation": exact_operation,
        "project_id": project_id.strip(),
        "authority_id": authority_id.strip(),
        "lane_id": lane_id.strip(),
        "sqlite_identity_sha256": sqlite_identity_sha256.strip().upper(),
        "source_sha256": source_sha256.strip().upper(),
        "top_k": int(top_k),
        "credential_names": list(_INDEXES[tool_id]["credential_names"]),
        "credential_values_read": False,
        "transports": list(_INDEXES[tool_id]["transports"]),
        "sqlite_remains_durable_authority": True,
        "index_is_rebuildable_from_sqlite_and_source_hashes": True,
        "project_truth_promotion_allowed": False,
        "cross_project_namespace_allowed": False,
        "changed_hash_only_write": exact_operation == "upsert_changed",
        "network_call_performed": False,
    }
    return {**body, "route_sha256": sha256_bytes(canonical_json_bytes(body))}


def bind_context_results_to_sqlite_identity(
    *,
    sqlite_identity_sha256: str,
    tool_id: str,
    result_ids: Iterable[str],
) -> dict[str, Any]:
    exact_hash = sqlite_identity_sha256.strip().upper()
    if _SHA256.fullmatch(exact_hash) is None:
        raise ValueError("Result binding requires an exact SQLite identity SHA-256.")
    if tool_id not in _INDEXES:
        raise ValueError(f"Unknown context index: {tool_id}")
    ids = list(dict.fromkeys(str(value).strip() for value in result_ids if str(value).strip()))
    body = {
        "schema": "evidence-lane.context-index-result-binding.v1",
        "status": "PASS",
        "tool_id": tool_id,
        "sqlite_identity_sha256": exact_hash,
        "result_ids": ids,
        "result_count": len(ids),
        "result_order_preserved": True,
        "results_are_evidence_locators_not_authority": True,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def context_index_catalog() -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.context-index-catalog.v1",
        "status": "PASS",
        "index_count": len(_INDEXES),
        "indexes": [
            {"tool_id": tool_id, **contract} for tool_id, contract in _INDEXES.items()
        ],
        "sqlite_remains_authority": True,
        "all_non_sqlite_indexes_are_rebuildable": True,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


__all__ = [
    "CONTEXT_INDEX_ROUTING_SCHEMA",
    "bind_context_results_to_sqlite_identity",
    "build_context_index_operation",
    "context_index_catalog",
]
