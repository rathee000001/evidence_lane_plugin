"""Real pinned Code model/grammar execution in an explicitly staged test bundle."""
from __future__ import annotations

import hashlib
import importlib.util
import os

import pytest

from .test_code_profile_v4 import call, create_plan, execute
from .test_code_profile_v4 import (
    code_system as code_system,  # noqa: PLC0414 - explicit pytest fixture re-export
)

pytestmark = pytest.mark.skipif(not os.environ.get('EVI_CODE_QUALIFICATION_ASSETS'),
    reason='Requires explicitly staged pinned Code qualification assets; absence is not runtime proof.')


@pytest.fixture(autouse=True)
def staged_assets(monkeypatch):
    root = os.environ.get('EVI_CODE_QUALIFICATION_ASSETS')
    if not root:
        return
    assert all(importlib.util.find_spec(name) for name in ('tree_sitter', 'tree_sitter_language_pack', 'sentence_transformers', 'sqlite_vec'))
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', root)
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    monkeypatch.setenv('HF_HUB_DISABLE_TELEMETRY', '1')


def test_real_typescript_and_rust_syntax_workers_use_verified_offline_grammars(code_system):
    source = code_system[1].source_root
    (source / 'api.ts').write_text('export function sayHello(name: string): string { return name; }\n', encoding='utf-8')
    (source / 'core.rs').write_text('pub fn measure() -> i32 { 42 }\n', encoding='utf-8')
    create_plan(code_system, ('code_index_syntax',))
    result = execute(code_system, 'code_index_syntax')
    for name in ('sayHello', 'measure'):
        page = call(code_system, 'code_query', {'snapshot_id': result['snapshot_id'], 'collection': 'symbols', 'query': name})
        assert page.status == 'ok', page.error
        assert page.result['result']['rows'][0]['fact']['parser'] == 'tree-sitter-language-pack'


def test_real_grammar_snapshot_reuse_preserves_index_and_checks_assets(code_system):
    source = code_system[1].source_root
    (source / 'api.ts').write_text('export function nativeGreet(name: string): string { return name; }\n', encoding='utf-8')
    create_plan(code_system, ('code_index_syntax', 'code_index_syntax'))
    first = execute(code_system, 'code_index_syntax')
    count = code_system[0].workers.status()['succeeded_operations']
    second = execute(code_system, 'code_index_syntax', {'paths': ['.'], 'expected_snapshot': first['snapshot_id']}, index=1)
    assert code_system[0].workers.status()['succeeded_operations'] == count
    assert second['snapshot_id'] == first['snapshot_id'] and second['refresh']['reused_files'] == 4
    assert second['refresh']['parser_worker_calls'] == 0 and second['refresh']['publication'] == 'reused_current_snapshot'
    page = call(code_system, 'code_query', {'snapshot_id': second['snapshot_id'], 'collection': 'symbols', 'query': 'nativeGreet'})
    assert page.status == 'ok' and page.result['result']['rows'][0]['fact']['parser'] == 'tree-sitter-language-pack'


def test_real_syntax_edit_keeps_grammar_parser_and_refreshes_automatically(code_system):
    source = code_system[1].source_root
    filename = source / 'api.ts'
    filename.write_text('export function firstName(name: string): string { return name; }\n', encoding='utf-8')
    create_plan(code_system, ('code_index_syntax', 'code_apply'))
    first = execute(code_system, 'code_index_syntax')
    result = execute(code_system, 'code_apply', {'snapshot_id': first['snapshot_id'], 'filename': 'api.ts',
        'expected_sha256': hashlib.sha256(filename.read_bytes()).hexdigest(),
        'replacement_utf8': 'export function currentName(name: string): string { return "Hi " + name; }\n'}, index=1)
    assert not result['index_refresh_required']
    assert result['index_refresh']['result']['refresh']['reused_files'] == 3
    page = call(code_system, 'code_query', {'snapshot_id': result['snapshot_id'], 'collection': 'symbols', 'query': 'currentName'})
    assert page.status == 'ok', page.error
    assert page.result['result']['rows'][0]['fact']['parser'] == 'tree-sitter-language-pack'
    history = call(code_system, 'code_query', {'snapshot_id': first['snapshot_id'], 'collection': 'symbols', 'query': 'firstName'})
    assert history.status == 'ok' and history.result['result']['rows']


def test_real_bge_embeddings_and_all_cosine_readers_preserve_project_bytes(code_system):
    from evidence_lane_plugin.shared_tool_assets import resolve_shared_asset
    resolve_shared_asset('embedding_snapshot')
    create_plan(code_system, ('code_index', 'code_semantic_index', 'code_semantic_index'))
    indexed = execute(code_system)
    embedded = execute(code_system, 'code_semantic_index', {'snapshot_id': indexed['snapshot_id'], 'chunk_limit': 2}, index=1, timeout=90)
    assert embedded['chunk_count'] == 2 and embedded['next_chunk_offset'] == 2 and embedded['fts_replaced'] is False
    store = code_system[1]
    before = {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    args = {'snapshot_id': indexed['snapshot_id'], 'embedding_run_id': embedded['embedding_run_id'],
            'query': 'greet a person with hello', 'limit': 3}
    python = call(code_system, 'code_semantic_query', args)
    assert python.status == 'ok', python.error
    vector = call(code_system, 'code_semantic_query_vec', args)
    assert vector.status == 'ok', vector.error
    faiss = call(code_system, 'code_semantic_query_faiss', args)
    assert faiss.status == 'ok', faiss.error
    assert faiss.result['result']['ranking'] == 'faiss_flat_cosine'
    assert faiss.result['result']['faiss_evidence']['engine'] == 'FAISS_INDEX_FLAT_INNER_PRODUCT'
    assert [row['chunk_id'] for row in python.result['result']['rows']] == [row['chunk_id'] for row in faiss.result['result']['rows']]
    for left, right in zip(python.result['result']['rows'], faiss.result['result']['rows'], strict=True):
        assert left['similarity'] == pytest.approx(right['similarity'], abs=0.000002)
    assert [row['chunk_id'] for row in python.result['result']['rows']] == [row['chunk_id'] for row in vector.result['result']['rows']]
    for left, right in zip(python.result['result']['rows'], vector.result['result']['rows'], strict=True):
        assert left['similarity'] == pytest.approx(right['similarity'], abs=0.000002)
    assert python.result['result']['worker_evidence']['worker_pid'] > 0
    assert before == {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    final = execute(code_system, 'code_semantic_index', {'snapshot_id': indexed['snapshot_id'], 'chunk_offset': 2}, index=2, timeout=90)
    assert final['chunk_count'] == 1 and final['next_chunk_offset'] is None
    with code_system[0].project_work.mutation(store) as lease, lease.transaction('local_code') as db:
        db.execute('UPDATE code_embedding SET vector=zeroblob(1536) WHERE run_id=?', (embedded['embedding_run_id'],))
    corrupted = call(code_system, 'code_semantic_query', args)
    assert corrupted.status == 'error' and corrupted.error.code == 'CODE_EMBEDDING_INTEGRITY'
