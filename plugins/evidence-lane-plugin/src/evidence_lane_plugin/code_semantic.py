"""Optional local embeddings alongside mandatory exact Code FTS retrieval.

Embeddings are derived, model-bound ranks. Exact chunk identities and text stay
in their Code lane. Queries do not change a Plan, index, model or source file.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
from concurrent.futures import TimeoutError as WorkerTimeout

from pydantic import Field

from .code_profile import (
    CodeResult,
    CodeSnapshot,
    _result,
    _snapshot,
    code_lane,
    require_code_calls,
    verify_snapshot_records,
)
from .code_profile_schema import code_migrations
from .compute_routes import ComputeContract
from .errors import LaneError
from .hashing import canonical_json_bytes
from .migrations import apply_migrations, read_compatibility
from .registry import ActionSpec
from .storage import json_text, now, project_snapshot
from .tool_routes import ToolRoute

DIMENSION = 384
MODEL_ID = 'BAAI/bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a'
EMBEDDING_COMPUTE = ComputeContract('code_embed_text', 'RETRIEVAL', ('CPU', 'NVIDIA_CUDA', 'AMD_ROCM'), 1024)


def semantic_migrations(lane_id):
    return code_migrations(lane_id)


class SemanticIndex(CodeSnapshot):
    chunk_offset: int = Field(default=0, ge=0, le=1_000_000)
    chunk_limit: int = Field(default=128, ge=1, le=512)


class SemanticQuery(CodeSnapshot):
    embedding_run_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    query: str = Field(min_length=1, max_length=4096)
    limit: int = Field(default=20, ge=1, le=50)
    max_bytes: int = Field(default=65_536, ge=2048, le=262_144)


def embedding_worker(arguments):
    from .semantic_retrieval import (
        configured_embedding_model,
        embed_with_local_sentence_transformer,
    )
    from .shared_tool_assets import resolve_shared_asset
    folder, before = resolve_shared_asset('embedding_snapshot')
    contract = configured_embedding_model()
    texts = arguments['texts']
    if contract.model_id != MODEL_ID or not 1 <= len(texts) <= 8 or sum(len(value) for value in texts) > 262_144:
        raise ValueError('CODE_EMBEDDING_INPUT_BUDGET')
    compute = arguments.get('_compute', {'selected_provider': 'CPU'})
    provider = compute['selected_provider']
    if provider != 'CPU':
        from .optional_runtimes import PROVIDERS, OptionalRuntime
        runtime = OptionalRuntime.from_worker_binding(compute)
        if PROVIDERS.get(runtime.runtime_id) != provider or provider not in EMBEDDING_COMPUTE.providers:
            raise LaneError('COMPUTE_WORKER_SCOPE', 'This embedding worker does not implement the selected provider.')
        output = runtime.execute('code_embed_text', {'texts': texts, 'model_id': MODEL_ID,
            'model_path': str(folder), 'model_files': before['files'], 'asset_identity': before['files_sha256']},
            device_id=compute['device_id'], device_index=compute['device_index'], required_vram_mib=compute['required_vram_mib'],
            server_binding=compute.get('provider_worker'))
        if resolve_shared_asset('embedding_snapshot')[1] != before:
            raise ValueError('CODE_EMBEDDING_MODEL_CHANGED')
        return output
    values = embed_with_local_sentence_transformer(texts, contract=contract)
    _, after = resolve_shared_asset('embedding_snapshot')
    if before != after:
        raise ValueError('CODE_EMBEDDING_MODEL_CHANGED')
    if len(values) != len(texts):
        raise ValueError('CODE_EMBEDDING_CARDINALITY')
    return {'model_id': contract.model_id, 'dimension': contract.dimension, 'vectors': values,
            'asset_identity': before['files_sha256'], 'device': 'cpu', 'network_downloads': False,
            'compute': {'selected_provider': 'CPU', 'execution_state': 'executed',
                'execution_basis': 'pinned_local_sentence_transformer_explicit_cpu_device'},
            'text_window': 'pinned_model_token_window; exact_full_text_remains_in_FTS'}


def _vector(values):
    if len(values) != DIMENSION or any(not math.isfinite(value) for value in values):
        raise LaneError('CODE_VECTOR_INVALID', 'The embedding dimensions or values violate the selected model contract.')
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0 or not math.isfinite(norm):
        raise LaneError('CODE_VECTOR_INVALID', 'A finite nonzero embedding is required.')
    return struct.pack('<384f', *(value / norm for value in values))


def _model_identity(value):
    if value.get('model_id') != MODEL_ID or value.get('dimension') != DIMENSION or value.get('network_downloads') is not False:
        raise LaneError('CODE_EMBEDDING_MODEL_MISMATCH', 'The worker did not use the pinned offline Code embedding model.')
    return value['asset_identity']


def index_semantics(context, request):
    execution, store = context.execution, context.execution.store
    lane = code_lane(store, request.lane_id)
    snapshot, source_manifest = _snapshot(lane, request.snapshot_id)
    if not verify_snapshot_records(lane, snapshot, source_manifest):
        raise LaneError('CODE_QUERY_INTEGRITY', 'The embedding input differs from its immutable Code snapshot.')
    with lane.connection(read_only=True) as connection:
        chunks = connection.execute('SELECT c.chunk_id,c.content_object FROM code_chunk c JOIN code_snapshot_file sf '
            'ON sf.version_id=c.version_id WHERE sf.snapshot_id=? ORDER BY sf.path,c.ordinal LIMIT ? OFFSET ?',
            (request.snapshot_id, request.chunk_limit + 1, request.chunk_offset)).fetchall()
    selected = chunks[:request.chunk_limit]
    if not selected:
        raise LaneError('CODE_EMBEDDING_EMPTY_SELECTION', 'Select an indexed Code chunk page containing text.')
    require_code_calls(execution, math.ceil(len(selected) / 8) + 1)
    computation = context.computation
    if computation is None:
        raise LaneError('COMPUTE_ROUTER_UNAVAILABLE', 'Enter semantic indexing through its registered compute adapter.')
    vectors, identity, compute_evidence, device = [], None, [], None
    for offset in range(0, len(selected), 8):
        batch = selected[offset:offset + 8]
        texts = [lane.read_object(row['content_object']).decode('utf-8') for row in batch]
        response = computation.submit('code_embed_text', {'texts': texts}).result()
        if response['status'] != 'ok':
            raise LaneError('CODE_EMBEDDING_WORKER_FAILED', 'The selected local embedding worker failed.')
        output = response['result']
        compute_evidence.append(computation.result(response))
        if device is not None and device != output['device']:
            raise LaneError('COMPUTE_RESULT_UNBOUND', 'The embedding worker changed device between batches.')
        device = output['device']
        observed = _model_identity(output)
        if identity is not None and observed != identity:
            raise LaneError('CODE_EMBEDDING_MODEL_CHANGED', 'The embedding asset changed between selected chunk batches.')
        identity = observed
        if len(output['vectors']) != len(batch):
            raise LaneError('CODE_EMBEDDING_CARDINALITY', 'The worker returned a different number of embeddings.')
        for row, value in zip(batch, output['vectors'], strict=True):
            vector = _vector(value)
            vectors.append((row['chunk_id'], vector, hashlib.sha256(vector).hexdigest()))
        execution.guard.observe(execution)
    manifest = {'schema': 'evidence-lane.code-embedding-page.v4', 'snapshot_id': request.snapshot_id,
        'model_id': MODEL_ID, 'dimension': DIMENSION, 'asset_identity': identity, 'created_at': now(),
        'chunk_offset': request.chunk_offset, 'chunk_limit': request.chunk_limit,
        'next_chunk_offset': request.chunk_offset + len(selected) if len(chunks) > request.chunk_limit else None,
        'chunks': [{'chunk_id': chunk, 'vector_sha256': digest} for chunk, _, digest in vectors],
        'semantic_authority': 'derived_model_rank', 'fts5_required': True, 'device': device,
        'compute': {'selection': computation.selection, 'worker_evidence': compute_evidence},
        'text_window': 'pinned_model_token_window; exact_full_text_remains_in_FTS'}
    run_id = hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
    execution._before_more_work()
    with execution.lease.coordinated_transaction([request.lane_id, 'receipts']):
        apply_migrations(lane, semantic_migrations(request.lane_id), writer=execution.lease)
        with lane.transaction() as connection:
            obj = lane.put_object(canonical_json_bytes(manifest))
            connection.execute('INSERT INTO code_embedding_run VALUES(?,?,?,?,?,?,?)',
                (run_id, request.snapshot_id, obj, MODEL_ID, identity, len(vectors), manifest['created_at']))
            connection.executemany('INSERT INTO code_embedding VALUES(?,?,?,?)',
                [(run_id, chunk, vector, digest) for chunk, vector, digest in vectors])
            store.append_receipt('code_embedding_index', {'lane_id': request.lane_id, 'run_id': run_id,
                'snapshot_id': request.snapshot_id, 'job_id': execution.claim.job_id, 'model_id': MODEL_ID})
    return _result(store, request.lane_id, 'code_semantic_index', {'embedding_run_id': run_id,
        'snapshot_id': request.snapshot_id, 'chunk_count': len(vectors), 'model_id': MODEL_ID,
        'next_chunk_offset': manifest['next_chunk_offset'], 'fts_replaced': False,
        'compute': manifest['compute']})


def _run(lane, run_id, snapshot_id):
    read_compatibility(lane, semantic_migrations(lane.lane_id))
    with lane.connection(read_only=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='code_embedding_run'").fetchone():
            raise LaneError('CODE_EMBEDDING_NOT_INDEXED', 'Index a bounded Code embedding page first.')
        row = connection.execute('SELECT * FROM code_embedding_run WHERE run_id=? AND snapshot_id=?', (run_id, snapshot_id)).fetchone()
    if row is None:
        raise LaneError('CODE_EMBEDDING_SCOPE', 'Select an embedding page belonging to this exact Code snapshot.')
    manifest = json.loads(lane.read_object(row['manifest_object']))
    if (hashlib.sha256(canonical_json_bytes(manifest)).hexdigest() != run_id or row['model_id'] != MODEL_ID
            or manifest['model_id'] != row['model_id'] or manifest['asset_identity'] != row['asset_identity']
            or manifest['snapshot_id'] != snapshot_id or len(manifest['chunks']) != row['chunk_count']):
        raise LaneError('CODE_EMBEDDING_INTEGRITY', 'The embedding page manifest failed its identity check.')
    return dict(row), manifest


def verify_semantics(context, request, output):
    lane = context.store.lane(request.lane_id)
    row, manifest = _run(lane, output.result['embedding_run_id'], request.snapshot_id)
    with lane.connection(read_only=True) as connection:
        vectors = connection.execute('SELECT chunk_id,vector,vector_sha256 FROM code_embedding WHERE run_id=? ORDER BY chunk_id', (row['run_id'],)).fetchall()
    observed = sorted((value['chunk_id'], hashlib.sha256(value['vector']).hexdigest()) for value in vectors)
    expected = sorted((value['chunk_id'], value['vector_sha256']) for value in manifest['chunks'])
    passed = observed == expected and len(vectors) == output.result['chunk_count']
    return [{'check_id': name, 'passed': passed, 'evidence': {'embedding_run_id': row['run_id'],
            'chunk_count': len(vectors), 'model_id': MODEL_ID}} for name in context.requested_checks]


def query_semantics(engine, context, request, *, sqlite_vec=False, faiss=False):
    store = engine.directory.open(context.project_id)
    with project_snapshot(store.root):
        lane = code_lane(store, request.lane_id)
        _snapshot(lane, request.snapshot_id)
        run, manifest = _run(lane, request.embedding_run_id, request.snapshot_id)
    if engine.workers is None or 'code_embed_text' not in engine.workers.operations:
        raise LaneError('CODE_EMBEDDING_WORKER_UNAVAILABLE', 'This host has no configured local embedding worker.')
    context.authorize('read')
    # A bounded pure query worker consumes no project writer or Plan transition.
    # It only accepts text, never a caller-selected module, path or command.
    computation = context.computation
    if computation is None:
        raise LaneError('COMPUTE_ROUTER_UNAVAILABLE', 'Enter semantic queries through their registered compute adapter.')
    future = computation.submit('code_embed_text', {'texts': [request.query]})
    try:
        response = future.result(timeout=30)
    except WorkerTimeout:
        raise LaneError('CODE_EMBEDDING_QUERY_TIMEOUT', 'The query worker exceeded its response budget; no automatic replay was issued.') from None
    if response['status'] != 'ok':
        raise LaneError('CODE_EMBEDDING_WORKER_FAILED', 'The selected local embedding query failed.')
    value = response['result']
    compute_evidence = computation.result(response)
    if _model_identity(value) != run['asset_identity'] or len(value['vectors']) != 1:
        raise LaneError('CODE_EMBEDDING_MODEL_CHANGED', 'The query model does not match the indexed embedding page.')
    query_blob = _vector(value['vectors'][0])
    query_vector = struct.unpack('<384f', query_blob)
    context.authorize('read')
    with project_snapshot(store.root):
        _run(lane, request.embedding_run_id, request.snapshot_id)
        with lane.connection(read_only=True) as connection:
            vector_version = None
            if sqlite_vec:
                from .semantic_retrieval import enable_sqlite_vector_extension
                vector_version = enable_sqlite_vector_extension(connection)
                sql = 'SELECT chunk_id,vector,vector_sha256,vec_distance_cosine(vector,?) AS distance FROM code_embedding WHERE run_id=?'
                rows = connection.execute(sql, (query_blob, request.embedding_run_id)).fetchall()
            else:
                rows = connection.execute('SELECT chunk_id,vector,vector_sha256 FROM code_embedding WHERE run_id=?', (request.embedding_run_id,)).fetchall()
            if len(rows) != run['chunk_count'] or len(rows) > 512:
                raise LaneError('CODE_EMBEDDING_INTEGRITY', 'The selected embedding page has invalid cardinality.')
            ranked, vector_page = [], []
            expected_vectors = {item['chunk_id']: item['vector_sha256'] for item in manifest['chunks']}
            for row in rows:
                if (hashlib.sha256(row['vector']).hexdigest() != row['vector_sha256']
                        or expected_vectors.get(row['chunk_id']) != row['vector_sha256']):
                    raise LaneError('CODE_EMBEDDING_INTEGRITY', 'An indexed vector failed its hash check.')
                values = struct.unpack('<384f', row['vector'])
                norm = math.sqrt(sum(v * v for v in values) * sum(v * v for v in query_vector))
                if norm <= 0 or not math.isfinite(norm):
                    raise LaneError('CODE_VECTOR_INVALID', 'The selected vector norm is invalid.')
                similarity = 1 - row['distance'] if sqlite_vec else sum(a * b for a, b in zip(values, query_vector, strict=True)) / norm
                if not math.isfinite(similarity):
                    raise LaneError('CODE_VECTOR_INVALID', 'The selected vector rank is non-finite.')
                ranked.append((round(similarity, 6), row['chunk_id']))
                vector_page.append((row['chunk_id'], list(values)))
            faiss_evidence = None
            if faiss:
                from .hybrid_retrieval import VectorRetrievalRequest, faiss_vector_rank
                faiss_evidence = faiss_vector_rank(VectorRetrievalRequest(
                    candidate_ids=[row[0] for row in vector_page], vectors=[row[1] for row in vector_page],
                    query_vector=list(query_vector), limit=request.limit))
                ranked = [(round(row['score'], 6), row['candidate_id']) for row in faiss_evidence['results']]
            ranked.sort(key=lambda value: (-value[0], value[1]))
            results = []
            for similarity, chunk_id in ranked[:request.limit]:
                row = connection.execute('SELECT sf.path,c.start_line,c.end_line,c.content_object FROM code_chunk c '
                    'JOIN code_snapshot_file sf ON sf.version_id=c.version_id WHERE c.chunk_id=? AND sf.snapshot_id=?',
                    (chunk_id, request.snapshot_id)).fetchone()
                if row is None:
                    raise LaneError('CODE_EMBEDDING_SCOPE', 'The ranked chunk is outside the selected Code snapshot.')
                results.append({'chunk_id': chunk_id, 'similarity': similarity, **dict(row)})
        body = {'snapshot_id': request.snapshot_id, 'embedding_run_id': request.embedding_run_id,
            'rows': results, 'model_id': MODEL_ID,
            'ranking': 'faiss_flat_cosine' if faiss else 'sqlite_vec_cosine' if sqlite_vec else 'python_cosine',
            'faiss_evidence': faiss_evidence,
            'sqlite_vec_version': vector_version,
            'coverage': {'chunk_offset': manifest['chunk_offset'], 'chunk_count': run['chunk_count'],
                         'next_chunk_offset': manifest['next_chunk_offset']},
            'worker_evidence': {'worker_pid': response.get('worker_pid'),
                'result_digest': hashlib.sha256(json_text(response).encode()).hexdigest(), 'device': value['device'],
                'compute': {'selection': computation.selection, 'worker_evidence': compute_evidence}},
            'mutation_performed': False, 'refresh_performed': False, 'semantic_authority': 'derived_model_rank'}
        action = 'code_semantic_query_faiss' if faiss else 'code_semantic_query_vec' if sqlite_vec else 'code_semantic_query'
        return _result(store, request.lane_id, action, body, limit=request.max_bytes)


def register_semantic_actions(engine):
    engine.registry.register(ActionSpec('code_semantic_index', 'Add a bounded optional embedding page over exact Code chunk identities using the pinned offline model.',
        SemanticIndex, CodeResult, index_semantics, permission='write', mutates=True, requires_delta=True,
        profile='code', workflow='source-intake', worker_operations=('code_embed_text',),
        verification_checks=('code_embedding_hashes_verified',), verifier=verify_semantics,
        tool_routes=(ToolRoute('code_semantic_index.local_model', index_semantics, ('Python', 'SentenceTransformers'),
            systems=('Windows',), compute=EMBEDDING_COMPUTE),)))
    def python_query(context, request):
        return query_semantics(engine, context, request)
    def vector_query(context, request):
        return query_semantics(engine, context, request, sqlite_vec=True)
    def faiss_query(context, request):
        return query_semantics(engine, context, request, faiss=True)
    for name, handler, tools in (('code_semantic_query', python_query, ('Python', 'SentenceTransformers')),
            ('code_semantic_query_vec', vector_query, ('Python', 'SentenceTransformers', 'sqlite_vec')),
            ('code_semantic_query_faiss', faiss_query, ('Python', 'SentenceTransformers', 'FAISS_CPU'))):
        engine.registry.register(ActionSpec(name, 'Rank a bounded model-matched Code embedding page without changing project state.',
            SemanticQuery, CodeResult, handler, profile='code', workflow='source-intake', queryable_in_delta=True,
            worker_operations=('code_embed_text',),
            read_migrations=(*semantic_migrations('local_code'), *semantic_migrations('github_code')),
            tool_routes=(ToolRoute(name + '.local_model', handler, tools, systems=('Windows',), compute=EMBEDDING_COMPUTE),)))
