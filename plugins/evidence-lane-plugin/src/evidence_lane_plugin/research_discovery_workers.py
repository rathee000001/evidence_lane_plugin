"""Owned search worker; discovered targets remain unvisited evidence locators."""
from .hashing import canonical_json_bytes
from .research_discovery import discover
from .research_web_content import sha
from .sector_evidence_parsers import EvidenceFacts


def discovery_facts(value, raw, name):
    facts = EvidenceFacts('research', name, raw)
    facts.items[0].update(origin='provider_search_response_bundle', target_documents_ingested=False)
    facts.add('research_question', 'query', 0, text=value['query'], origin='user_search_query')
    for ordinal, item in enumerate(value['results']):
        part = 'results/' + str(ordinal)
        citation_id = 'websrc_' + sha(canonical_json_bytes({key: item[key] for key in ('title', 'url', 'snippet')}))[:24]
        facts.text(part, 0, item['title'] + '\n' + item['snippet'], url=item['url'],
            providers=item['providers'], target_status='unvisited_search_result',
            assertion_status='source_assertion_unvalidated', citation_id=citation_id,
            title_truncated=item['title_truncated'], snippet_truncated=item['snippet_truncated'])
        facts.add('research_citation', part + '/citation', 0, url=item['url'], citation_id=citation_id,
            providers=item['providers'], target_status='unvisited_search_result', assertion_status='source_assertion_unvalidated')
    facts.add('research_receipt', 'provider_receipt', 0, attempts=value['attempts'], status=value['status'],
        result_count=value['result_count'], requests=value['capture']['requests'], versions=value['versions'])
    return facts.finish('ddgs_bounded_provider_search', {'target_documents_ingested': False,
        'network_discovery': True, 'provider_status': value['status'], 'results_truncated': value['results_truncated']}, [
        'Provider snippets are unvalidated search evidence; discovered URLs were not visited or ingested.',
        'The stored original is a provider-query response bundle, not the discovered target documents.',
        'Selected provider failures are explicit; no request retry or post-failure adapter switch occurs.'])


def discover_source(arguments):
    value = discover(**arguments['query_arguments'])
    if all(row['status'] == 'failed' for row in value['attempts']):
        raise ValueError('RESEARCH_DISCOVERY_ALL_PROVIDERS_FAILED')
    raw = canonical_json_bytes(value)
    if len(raw) > 8_388_608:
        raise ValueError('RESEARCH_DISCOVERY_BUNDLE_BUDGET')
    return {'discovery': value, 'facts': discovery_facts(value, raw, arguments['logical_name'])}


def discovery_worker_operations():
    from .workers import WorkerOperation
    return (WorkerOperation('research_discover_sources', __name__, 'discover_source', max_output_bytes=16_777_216),)
