"""DDGS provider parsing over explicitly bounded Research transport."""
from __future__ import annotations

import importlib.metadata
import inspect
from pathlib import Path

from .research_discovery_transport import ProviderTransport, budget
from .research_web_content import safe_url, sha
from .research_web_fetch import before

BACKENDS = ('brave', 'duckduckgo', 'google', 'mojeek', 'startpage', 'wikipedia', 'yahoo')
ROOTS = {'brave': ('brave.com',), 'duckduckgo': ('duckduckgo.com',), 'google': ('google.com',),
    'mojeek': ('mojeek.com',), 'startpage': ('startpage.com',), 'wikipedia': ('wikipedia.org',), 'yahoo': ('yahoo.com',)}


def bounded_text(text, max_bytes):
    raw = text.encode('utf-8')
    return raw[:max_bytes].decode('utf-8', errors='ignore'), len(raw) > max_bytes


def discover(query, *, backends=('duckduckgo', 'brave'), region='us-en', safesearch='moderate',
        timelimit=None, page=1, limit=10, timeout_seconds=30, max_bytes=2_097_152, max_requests=8):
    if (not isinstance(query, str) or not 1 <= len(query) <= 500 or '\x00' in query
            or not 1 <= len(backends) <= 4 or len(set(backends)) != len(backends) or set(backends) - set(BACKENDS)
            or not 1 <= page <= 10 or not 1 <= limit <= 50 or not 1 <= timeout_seconds <= 60
            or not 1024 <= max_bytes <= 2_097_152 or not 1 <= max_requests <= 16
            or safesearch not in {'on', 'moderate', 'off'} or timelimit not in {None, 'd', 'w', 'm', 'y'}):
        raise ValueError('RESEARCH_DISCOVERY_ARGUMENTS')
    import re
    if not re.fullmatch('[a-z]{2}-[a-z]{2}', region):
        raise ValueError('RESEARCH_DISCOVERY_REGION')
    if importlib.metadata.version('ddgs') != '9.16.0':
        raise ValueError('RESEARCH_DISCOVERY_ADAPTER_VERSION')
    from ddgs.base import BaseSearchEngine
    from ddgs.engines import ENGINES
    from ddgs.results import ResultsAggregator
    from ddgs.similarity import SimpleFilterRanker
    deadline, selected_budget = budget(timeout_seconds, max_bytes, max_requests)
    aggregator = ResultsAggregator({'href'})
    attempts, sources, attributions = [], {}, {}
    for backend in backends:
        before(deadline)
        kind = ENGINES['text'].get(backend)
        if kind is None or kind.search is not BaseSearchEngine.search:
            raise ValueError('RESEARCH_DISCOVERY_PROVIDER_CONTRACT')
        transport = ProviderTransport(deadline=deadline, budget=selected_budget, backend=backend, roots=ROOTS[backend])

        class SelectedProvider(kind):
            def __init__(self, selected_transport):
                self.http_client = selected_transport
                self.http_client.client.headers_update(self.headers_update)
                self.results = []

        provider = SelectedProvider(transport)
        for target in (kind, BaseSearchEngine, ResultsAggregator, SimpleFilterRanker):
            path = Path(inspect.getsourcefile(target))
            sources[path.name] = sha(path.read_bytes())
        try:
            rows = provider.search(query, region=region, safesearch=safesearch, timelimit=timelimit, page=page) or []
            before(deadline)
            if len(rows) > 2000:
                raise ValueError('RESEARCH_DISCOVERY_ROW_BUDGET')
            accepted, rejected = [], 0
            for row in rows:
                try:
                    row.href = safe_url(row.href)
                except ValueError:
                    rejected += 1
                    continue
                row.title, title_truncated = bounded_text(row.title, 4096)
                row.body, snippet_truncated = bounded_text(row.body, 16384)
                row.title_truncated, row.snippet_truncated = title_truncated, snippet_truncated
                attributions.setdefault(row.href, []).append(backend)
                accepted.append(row)
            aggregator.extend(accepted)
            attempts.append({'backend': backend, 'status': 'results' if accepted else 'empty',
                'provider_rows': len(rows), 'invalid_urls_rejected': rejected, 'usable_rows': len(accepted)})
        except Exception as error:  # noqa: BLE001 - provider failure is data; do not retry or expose vendor messages
            code = str(error) if isinstance(error, ValueError) and re.fullmatch('RESEARCH_[A-Z_]{1,70}', str(error)) else 'RESEARCH_DISCOVERY_PROVIDER_FAILED'
            attempts.append({'backend': backend, 'status': 'failed', 'code': code})
            if code in {'RESEARCH_WEB_DEADLINE', 'RESEARCH_DISCOVERY_REQUEST_BUDGET', 'RESEARCH_DISCOVERY_RESPONSE_BUDGET'}:
                raise ValueError(code) from None
    values = SimpleFilterRanker().rank(aggregator.extract_dicts(), query)
    results = []
    for value in values[:limit]:
        results.append({'title': value['title'], 'url': value['href'], 'snippet': value['body'],
            'title_truncated': value.get('title_truncated', False), 'snippet_truncated': value.get('snippet_truncated', False),
            'providers': list(dict.fromkeys(attributions[value['href']])), 'target_status': 'unvisited_search_result',
            'source_assertions_validated': False})
    return {'schema': 'evidence-lane.research-discovery.v4', 'query': query, 'backends': list(backends),
        'region': region, 'safesearch': safesearch, 'timelimit': timelimit, 'page': page,
        'limit': limit, 'timeout_seconds': timeout_seconds, 'max_bytes': max_bytes, 'max_requests': max_requests,
        'results': results, 'attempts': attempts, 'result_count': len(results), 'results_truncated': len(values) > limit,
        'status': 'partial' if any(row['status'] == 'failed' for row in attempts) or len(attempts) != len(backends) else 'complete',
        'capture': selected_budget, 'versions': {name: importlib.metadata.version(name) for name in ('ddgs', 'httpx', 'lxml')},
        'provider_source_sha256': sources, 'network_used': True, 'automatic_target_ingestion': False,
        'automatic_retry': False, 'fallback_after_invocation': False, 'transport': 'HTTPX_bounded_stream',
        'ddgs_role': 'pinned_provider_payload_parsing_normalization_deduplication_and_ranking',
        'remote_currentness': 'provider_responses_observed_once_not_rechecked',
        'deadline_scope': 'checked_at_request_and_parse_boundaries_not_a_hard_process_deadline'}
