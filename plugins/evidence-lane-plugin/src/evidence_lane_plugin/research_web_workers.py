"""Owned web workers: selected network transport or offline saved-byte extraction."""
from __future__ import annotations

import base64

from .research_web_content import MAX_TEXT, extract, sha
from .research_web_fetch import capture
from .sector_evidence_parsers import EvidenceFacts


def facts_for(body, captured, extracted):
    url = captured['requested_url']
    facts = EvidenceFacts('research', url, body)
    source = facts.items[0]
    source.pop('path')
    source.pop('extension')
    source.update(url=url, final_url=captured['final_url'], captured_at=captured['captured_at'],
        origin='observed_http_response', assertion_status='source_assertion_unvalidated')
    facts.text('response/text', 0, extracted['text'], assertion_status='source_assertion_unvalidated')
    facts.labels('response/text', extracted['text'])
    for ordinal, link in enumerate(extracted['links']):
        facts.add('research_citation', 'response/links', ordinal, **link,
            assertion_status='source_assertion_unvalidated')
    facts.add('research_receipt', 'response/receipt', 0, capture={key: captured[key] for key in (
        'requested_url', 'final_url', 'captured_at', 'completed_at', 'status_code', 'body_sha256', 'wire_sha256',
        'transport', 'remote_currentness')}, extractor=extracted['extractor'],
        citation_resolution=extracted.get('citation_resolution'))
    return facts.finish(extracted['extractor'], {'network_capture': True, 'scripts_executed': False,
        'extraction_status': extracted['status'], 'links_truncated': extracted['links_truncated'],
        'citation_resolution': extracted.get('citation_resolution'),
        'remote_currentness': captured['remote_currentness']}, [
        'One observed response chain; no assertion validation, script execution or child-resource fetching.',
        'Text is a selected algorithm projection; the exact response and decoded body are retained.',
        'HTML citation URLs are static HTTP(S) resource locators without fragments; unsupported references are disclosed as omissions.',
        'The bounded HTML scan does not reproduce browser DOM repair, CSP policy or script-driven base changes.'])


def capture_source(arguments, *, _allow_non_public_for_tests=False):
    captured = capture(arguments['url'], arguments['selected_transport'], max_bytes=arguments['max_bytes'],
        timeout_seconds=arguments['timeout_seconds'], max_redirects=arguments['max_redirects'],
        _allow_non_public_for_tests=_allow_non_public_for_tests)
    body = base64.b64decode(captured['body_base64'], validate=True)
    media = arguments['media']
    if media == 'auto':
        content_type = captured['headers'].get('content-type', '').split(';', 1)[0].strip().lower()
        media = ('html' if content_type in {'text/html', 'application/xhtml+xml'} else
            'text' if content_type.startswith('text/') or content_type in {'application/json', 'application/xml'} else 'opaque')
    if media == 'html':
        extracted = extract(body, 'stdlib', source_url=captured['final_url'], encoding=arguments['encoding'])
    else:
        text = body.decode(arguments['encoding'], errors='strict') if media == 'text' else ''
        if len(text.encode()) > MAX_TEXT:
            raise ValueError('RESEARCH_WEB_TEXT_BUDGET')
        extracted = {'schema': 'evidence-lane.research-web-extraction.v4', 'extractor': media,
            'status': 'extracted' if text else 'opaque' if media == 'opaque' else 'empty',
            'text': text, 'markdown': '', 'text_sha256': sha(text.encode()), 'source_sha256': sha(body),
            'source_url': captured['final_url'], 'encoding': arguments['encoding'], 'versions': {},
            'links': [], 'links_truncated': False, 'network_used': False, 'scripts_executed': False,
            'source_assertions_validated': False, 'fallback_after_invocation': False,
            'fidelity': 'saved_text_bytes' if media == 'text' else 'exact_opaque_response_bytes'}
    return {'capture': captured, 'extraction': extracted, 'media': media,
        'facts': facts_for(body, captured, extracted)}


def extract_source(arguments):
    body = base64.b64decode(arguments['body_base64'], validate=True)
    extracted = extract(body, arguments['extractor'], source_url=arguments['capture']['final_url'],
        encoding=arguments['encoding'])
    return {'extraction': extracted, 'facts': facts_for(body, arguments['capture'], extracted),
        'body_sha256': sha(body), 'network_used': False}


def web_worker_operations():
    from .workers import WorkerOperation
    return (WorkerOperation('research_web_capture_source', __name__, 'capture_source', max_output_bytes=41_943_040),
        WorkerOperation('research_web_extract_source', __name__, 'extract_source',
            max_input_bytes=16_777_216, max_output_bytes=16_777_216))
