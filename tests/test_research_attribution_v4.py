"""Exact saved-page citation destinations, including document base URLs."""
import base64

import pytest
from evidence_lane_plugin.research_web_content import EXTRACTORS, extract

from .test_evidence_sectors_v4 import call, execute, plan
from .test_research_web_v4 import TOOLS
from .test_research_web_v4 import server as server  # noqa: PLC0414
from .test_research_web_v4 import system as system  # noqa: PLC0414


@pytest.mark.parametrize('extractor', EXTRACTORS)
def test_relative_citation_uses_first_document_base(extractor):
    source = (b'<html><head><base href="https://reference.example/studies/">'
        b'<base href="https://wrong.example/"></head><body><article>'
        + b'<p>Evidence about the source observation and its limitations.</p>' * 20
        + b'<a href="trial">Trial source</a></article></body></html>')
    result = extract(source, extractor, source_url='https://publisher.example/articles/page')
    assert [row['url'] for row in result['links']] == ['https://reference.example/studies/trial']
    assert not result['network_used'] and not result['source_assertions_validated']
    assert result['citation_resolution']['base_url'] == 'https://reference.example/studies/'
    assert result['citation_resolution']['basis'] == 'first_base_href'


@pytest.mark.parametrize('markup,expected,basis,omitted', [
    ('<base href="../refs/"><a href="trial">a</a>', ['https://publisher.example/refs/trial'], 'first_base_href', 0),
    ('<a href="trial">a</a><base href="https://reference.example/">', ['https://reference.example/trial'], 'first_base_href', 0),
    ('<base target="_blank"><base href="/refs/"><a href="trial">a</a>', ['https://publisher.example/refs/trial'], 'first_base_href', 0),
    ('<base href=""><base href="https://wrong.example/"><a href="trial">a</a>', ['https://publisher.example/articles/trial'], 'first_base_href', 0),
    ('<template><base href="https://wrong.example/"></template><a href="trial">a</a>', ['https://publisher.example/articles/trial'], 'response_url', 0),
    ('<base href="file:///private/"><base href="https://wrong.example/"><a href="trial">a</a><a href="https://reference.example/trial">b</a>', ['https://reference.example/trial'], 'unsupported_first_base_href', 1),
    ('<base href="https://user:secret@private.example/"><a href="trial">a</a>', [], 'unsupported_first_base_href', 1),
    ('<base href="https://reference.example/" href="https://wrong.example/"><a href="trial" href="other">a</a>', ['https://reference.example/trial'], 'first_base_href', 0),
    ('<a href="javascript:alert(1)">a</a><a href="https://reference.example/trial#result">b</a>', ['https://reference.example/trial'], 'response_url', 1),
])
def test_static_base_reference_bounds_are_explicit(markup, expected, basis, omitted):
    result = extract(markup.encode(), 'stdlib', source_url='https://publisher.example/articles/page')
    assert [row['url'] for row in result['links']] == expected
    assert result['citation_resolution']['basis'] == basis
    assert result['citation_resolution']['omitted_links'] == omitted


def test_links_are_bounded_before_final_resolution():
    source = (b'<a href="trial">a</a>' * 2001) + b'<base href="https://reference.example/">'
    result = extract(source, 'stdlib', source_url='https://publisher.example/page')
    assert result['links_truncated'] and len(result['links']) == 2000
    assert all(row['url'] == 'https://reference.example/trial' for row in result['links'])


def test_engine_citations_and_refresh_keep_exact_observed_source(system, server, monkeypatch):
    from evidence_lane_plugin.research_web_profile import read_snapshot

    url, events = server
    original = b'<head><base href="https://reference.example/first/"></head><p>Source observation.</p><a href="trial">Citation</a>'
    monkeypatch.setattr('tests.test_research_web_v4.HTML', original)
    actions = ['research_web_capture', 'research_web_extract', 'research_web_capture']
    plan(system, 'research', actions, tools=TOOLS)
    first = execute(system, actions[0], {'url': url + '/ok'})
    extracted = execute(system, actions[1], {'snapshot_id': first['snapshot_id'],
        'expected_snapshot': first['snapshot_id'], 'extractor': 'beautifulsoup'}, index=1)
    assert len(events) == 1
    assert extracted['capture_snapshot'] == first['snapshot_id']
    changed = original.replace(b'/first/', b'/second/')
    monkeypatch.setattr('tests.test_research_web_v4.HTML', changed)
    refreshed = execute(system, actions[2], {'url': url + '/ok', 'expected_snapshot': extracted['snapshot_id']}, index=2)
    assert refreshed['generation'] == 3 and len(events) == 2
    store = system[1]
    before = {str(path): path.read_bytes() for path in store.root.rglob('*.sqlite')}
    for observed, raw, destination in [(first, original, 'first'), (extracted, original, 'first'), (refreshed, changed, 'second')]:
        citations = call(system, 'research_query', {'snapshot_id': observed['snapshot_id'], 'collection': 'research_citation'})
        assert citations.status == 'ok', citations.error
        assert [(row['url'], row['assertion_status']) for row in citations.result['result']['rows']] == [
            (f'https://reference.example/{destination}/trial', 'source_assertion_unvalidated')]
        manifest, facts, parsed, body, _ = read_snapshot(store, observed['snapshot_id'])
        assert body == raw and manifest['url'] == url + '/ok'
        assert facts['fidelity']['citation_resolution'] == parsed['citation_resolution']
        assert parsed['citation_resolution']['basis'] == 'first_base_href'
        fetched = call(system, 'lane_fetch', {'lane_id': 'research', 'snapshot_id': observed['snapshot_id'],
            'path': observed['logical_name'], 'representation': 'original_source'})
        assert fetched.status == 'ok', fetched.error
        assert base64.b64decode(fetched.result['result']['read']['result']['content_base64']) == raw
    assert call(system, 'research_current').result['result']['files'][0]['snapshot_id'] == refreshed['snapshot_id']
    assert before == {str(path): path.read_bytes() for path in store.root.rglob('*.sqlite')}
    assert len(events) == 2 and all(event['path'] == '/ok' for event in events)


def test_metadata_budget_keeps_complete_citation_provenance(system, server, monkeypatch):
    from evidence_lane_plugin.hashing import canonical_json_bytes

    url, events = server
    base = 'https://reference.example/' + 'a' * 3500 + '/'
    monkeypatch.setattr('tests.test_research_web_v4.HTML', f'<base href="{base}"><p>Recorded evidence</p><a href="trial">Source</a>'.encode())
    plan(system, 'research', ['research_web_capture'], tools=TOOLS)
    captured = execute(system, 'research_web_capture', {'url': url + '/ok'})
    store = system[1]
    before = {str(path): path.read_bytes() for path in store.root.rglob('*.sqlite')}
    denied = call(system, 'research_query', {'snapshot_id': captured['snapshot_id'], 'collection': 'metadata', 'max_bytes': 2048})
    assert denied.status == 'error' and denied.error.code == 'SECTOR_EVIDENCE_QUERY_ITEM_BUDGET'
    accepted = call(system, 'research_query', {'snapshot_id': captured['snapshot_id'], 'collection': 'metadata', 'max_bytes': 8192})
    assert accepted.status == 'ok', accepted.error
    assert accepted.result['result']['fidelity']['citation_resolution']['base_url'] == base
    assert 2048 < len(canonical_json_bytes(accepted.result['result'])) <= 8192
    assert before == {str(path): path.read_bytes() for path in store.root.rglob('*.sqlite')}
    assert len(events) == 1
