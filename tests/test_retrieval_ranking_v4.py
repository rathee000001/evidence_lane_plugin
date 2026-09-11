"""Real bounded ranking over immutable owner reads, with distinct authority scope."""
import copy
import hashlib
import math

import pytest
from evidence_lane_plugin.hashing import canonical_json_bytes
from evidence_lane_plugin.hybrid_retrieval import (
    RetrievalCandidate,
    VectorRetrievalRequest,
    faiss_vector_rank,
    rank_bm25_candidates,
    rank_owner_candidates,
    reciprocal_rank_fusion,
    tfidf_candidates,
)
from pydantic import ValidationError

from tests.test_code_profile_v4 import call, code_system, create_plan, execute
from tests.test_project_search_v4 import bytes_digest

__all__ = ['code_system']


@pytest.mark.parametrize('method', ['fts5', 'bm25', 'tfidf', 'hybrid', 'rank_bm25'])
def test_lane_ranking_preserves_exact_owner_rows_and_selected_result_limit(code_system, method):
    create_plan(code_system)
    indexed = execute(code_system)
    store = code_system[1]
    before = bytes_digest(store.root)
    direct = call(code_system, 'code_query', {'snapshot_id': indexed['snapshot_id'],
        'query': 'greeting', 'match_mode': 'any', 'limit': 20})
    page = call(code_system, 'lane_search', {'lane_id': 'local_code', 'query': 'greeting',
        'retrieval': method, 'limit': 1, 'candidate_limit': 20})
    assert page.status == 'ok', page.error
    arm = page.result['arms'][0]
    result = arm['queries'][0]
    assert arm['hit_count'] == 1 and arm['truncated']
    assert result['snapshot_id'] == indexed['snapshot_id']
    assert result['owner_result_unchanged']
    if method in {'tfidf', 'hybrid', 'rank_bm25'}:
        assert result['result'] == direct.result
        rows = result['result']['result']['rows']
        ranking = result['ranking']
        assert ranking['candidate_count'] == 2 and len(ranking['selected']) == 1
        selected = ranking['selected'][0]
        assert selected['row_sha256'] == hashlib.sha256(canonical_json_bytes(rows[selected['owner_row_index']])).hexdigest().upper()
        assert ranking['candidate_scope'] == 'bounded_owner_fts5_bm25_page'
    else:
        assert result['ranking'] is None
    assert before == bytes_digest(store.root)


def test_tfidf_and_fusion_have_independently_calculated_page_scores():
    candidates = [RetrievalCandidate(candidate_id='a', text='alpha alpha beta'),
        RetrievalCandidate(candidate_id='b', text='alpha beta beta beta'),
        RetrievalCandidate(candidate_id='c', text='gamma')]
    rows = tfidf_candidates('alpha', candidates)
    assert [row['candidate_id'] for row in rows] == ['a', 'b', 'c']
    assert rows[0]['score'] == pytest.approx(2 / 3 * (math.log(4 / 3) + 1))
    assert rows[1]['score'] == pytest.approx(1 / 4 * (math.log(4 / 3) + 1))
    fusion = reciprocal_rank_fusion([['a', 'b'], ['b', 'a']])
    assert fusion == [{'candidate_id': key, 'score': 1 / 61 + 1 / 62} for key in ('a', 'b')]
    with pytest.raises(ValueError, match='RRF_BOUND_INVALID'):
        reciprocal_rank_fusion([['a', 'a']])
    raw = [{'text': value.text, 'exact_source': value.candidate_id} for value in candidates]
    unchanged = copy.deepcopy(raw)
    result = rank_owner_candidates('alpha', raw, text_field='text', method='hybrid', limit=2)
    assert raw == unchanged and len(result['selected']) == 2


def test_ranking_bounds_and_missing_dependency_never_use_overlap_fallback(monkeypatch):
    import evidence_lane_plugin.hybrid_retrieval as module
    candidates = [RetrievalCandidate(candidate_id='a', text='alpha')]
    monkeypatch.setattr(module.importlib.util, 'find_spec', lambda name: None)
    with pytest.raises(RuntimeError, match='RANK_BM25_DEPENDENCY_UNAVAILABLE'):
        rank_bm25_candidates('alpha', candidates)
    with pytest.raises(ValueError, match='LEXICAL_CANDIDATE_BUDGET'):
        tfidf_candidates('alpha', candidates * 101)
    with pytest.raises(ValueError, match='LEXICAL_OWNER_TEXT_MISSING'):
        rank_owner_candidates('alpha', [{'label': 'not admitted text'}], text_field='text', method='tfidf', limit=1)


def test_reranking_preserves_authority_boundaries_and_rejects_oversized_query(code_system, monkeypatch):
    create_plan(code_system)
    execute(code_system)
    before = bytes_digest(code_system[1].root)
    result = call(code_system, 'search', {'query': 'greeting', 'lane_ids': ['local_code', 'memory'], 'retrieval': 'hybrid'})
    assert result.status == 'ok', result.error
    assert result.result['arms'][0]['state'] == 'hit'
    assert result.result['arms'][1]['reason'] == 'requested_ranking_not_supported'
    assert not result.result['complete_within_selected_scope']
    invalid = call(code_system, 'lane_search', {'lane_id': 'local_code', 'query': 'greeting',
        'retrieval': 'tfidf', 'candidate_limit': 1, 'limit': 2})
    assert invalid.error.code == 'INVALID_ARGUMENTS'
    router = code_system[0].registry.tool_router
    original = router.observer
    monkeypatch.setattr(router, 'observer', lambda tool, context: {
        'tool_id': tool, 'ready': False, 'basis': 'injected_missing_dependency'}
        if tool == 'rank_bm25' else original(tool, context))
    unavailable = call(code_system, 'lane_search', {'lane_id': 'local_code', 'query': 'greeting', 'retrieval': 'rank_bm25'})
    assert unavailable.error.code == 'TOOL_ROUTE_UNAVAILABLE'
    assert bytes_digest(code_system[1].root) == before


@pytest.mark.parametrize('vector', [[float('nan'), 1], [float('inf'), 0], [0, 0], [1], [1e30, 0]])
def test_faiss_rejects_unusable_vectors_before_native_code(vector):
    with pytest.raises(ValidationError):
        VectorRetrievalRequest(candidate_ids=['a'], vectors=[vector], query_vector=[1, 0])


def test_real_faiss_normalizes_exact_vectors_and_keeps_deterministic_ties():
    request = VectorRetrievalRequest(candidate_ids=['b', 'a', 'c'],
        vectors=[[2, 0], [1, 0], [0, 4]], query_vector=[5, 0], limit=1)
    result = faiss_vector_rank(request)
    assert result['engine'] == 'FAISS_INDEX_FLAT_INNER_PRODUCT'
    assert result['results'] == [{'candidate_id': 'a', 'score': 1.0}]
    assert not result['persistent_authority']
    assert request.vectors == [[2, 0], [1, 0], [0, 4]]
    with pytest.raises(ValidationError):
        VectorRetrievalRequest(candidate_ids=['a', 'a'], vectors=[[1, 0], [1, 0]], query_vector=[1, 0])
