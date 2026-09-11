"""Pinned DDGS parsing, bounded requests and Research-owned publication."""
from __future__ import annotations

import base64
import hashlib
import json
import os

import httpx
import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.research_discovery import BACKENDS, discover
from evidence_lane_plugin.research_discovery_transport import ProviderTransport, budget
from evidence_lane_plugin.research_web_workers import web_worker_operations
from evidence_lane_plugin.sector_evidence_workers import evidence_worker_operations
from evidence_lane_plugin.workers import WorkerOperation, WorkerPool

from .research_discovery_fixtures import provider_response
from .test_evidence_sectors_v4 import call, execute, plan
from .test_research_web_v4 import server as server  # noqa: PLC0414

TOOLS = ('Python', 'DDGS', 'HTTPX', 'lxml', 'SQLite_FTS5_BM25')


@pytest.fixture
def synthetic_provider(monkeypatch):
    original = httpx.Client
    events = []

    def handler(request):
        events.append({'url': str(request.url), 'method': request.method,
            'cookies': request.headers.get('cookie'), 'payload': request.content.decode()})
        return provider_response(request)

    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, 'Client', client)
    return events


@pytest.fixture
def system(tmp_path, request):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'note.md').write_text('Evidence: independent local SQLite source\n', encoding='utf-8')
    # The fixture replaces only the external HTTP provider responses. The owned
    # process runs the canonical adapter, parser and facts construction.
    operations = (*web_worker_operations(), *evidence_worker_operations(),
        WorkerOperation('research_discover_sources', 'tests.research_discovery_fixtures', 'fixture_worker', max_output_bytes=16_777_216))
    with Engine(tmp_path / 'runtime', worker_pool=WorkerPool(operations, workers=1)) as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        permissions = ['read', 'write', 'tools', 'admin']
        if getattr(request, 'param', 'network') == 'network':
            permissions.append('network')
        _, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=store.project_id, permissions=permissions)]))
        yield engine, store, session


@pytest.mark.parametrize('backend', BACKENDS)
def test_actual_provider_payload_parser_and_ranker(backend, synthetic_provider):
    result = discover('SQLite', backends=[backend])
    assert result['result_count'] == 1 and result['status'] == 'complete'
    item = result['results'][0]
    assert item['url'] == 'https://example.test/sqlite' and item['providers'] == [backend]
    assert item['target_status'] == 'unvisited_search_result' and not result['automatic_target_ingestion']
    assert len(synthetic_provider) == (2 if backend in {'startpage', 'wikipedia'} else 1)
    assert all('example.test' not in event['url'] for event in synthetic_provider)
    assert result['provider_source_sha256'] and result['versions']['ddgs'] == '9.16.0'


def test_selected_provider_failures_are_explicit_not_retried(synthetic_provider):
    result = discover('SQLite fixture_brave_failed', backends=['duckduckgo', 'brave'])
    assert result['status'] == 'partial' and result['result_count'] == 1
    assert [row['status'] for row in result['attempts']] == ['results', 'failed']
    assert result['attempts'][1]['code'] == 'RESEARCH_DISCOVERY_HTTP_STATUS'
    assert len(synthetic_provider) == 2


def test_deduplication_retains_all_provider_attributions(synthetic_provider):
    result = discover('SQLite', backends=['duckduckgo', 'brave', 'mojeek'])
    assert result['result_count'] == 1
    assert result['results'][0]['providers'] == ['duckduckgo', 'brave', 'mojeek']
    assert len(synthetic_provider) == 3


def test_provider_request_bound_fails_without_trying_more_backends(synthetic_provider):
    with pytest.raises(ValueError, match='RESEARCH_DISCOVERY_REQUEST_BUDGET'):
        discover('SQLite', backends=['duckduckgo', 'brave'], max_requests=1)
    assert len(synthetic_provider) == 1


def test_provider_transport_rejects_foreign_host_and_ambient_auth_before_request(synthetic_provider):
    deadline, selected = budget(10, 2048, 3)
    transport = ProviderTransport(deadline=deadline, budget=selected, backend='duckduckgo', roots=('duckduckgo.com',))
    with pytest.raises(ValueError, match='PROVIDER_REDIRECT'):
        transport.request('GET', 'https://example.test/unauthorized')
    with pytest.raises(ValueError, match='PROVIDER_HEADERS'):
        transport.headers_update({'Authorization': 'never-send'})
    assert not synthetic_provider


def test_discovery_bounded_snapshot_refresh_and_shared_reader(system):
    from evidence_lane_plugin.research_discovery_profile import read_snapshot
    actions = ['research_web_discover', 'research_index', 'research_web_discover']
    plan(system, 'research', actions, tools=TOOLS)
    first = execute(system, actions[0], {'query': 'SQLite', 'backends': ['duckduckgo', 'brave']})
    assert first['result_count'] == 1 and first['status'] == 'complete'
    manifest, facts, value, raw = read_snapshot(system[1], first['snapshot_id'])
    assert manifest['worker_envelope']['worker_pid'] != os.getpid()
    assert not facts['fidelity']['target_documents_ingested'] and value['result_count'] == 1
    citations = call(system, 'research_query', {'snapshot_id': first['snapshot_id'], 'collection': 'research_citation'})
    assert citations.status == 'ok', citations.error
    # Original canonical citation input is sorted UTF-8 JSON plus a newline.
    identity_bytes = (json.dumps({key: value['results'][0][key] for key in ('title', 'url', 'snippet')},
        sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n').encode()
    expected_citation = 'websrc_' + hashlib.sha256(identity_bytes).hexdigest()[:24]
    citation = citations.result['result']['rows'][0]
    assert citation['citation_id'] == expected_citation
    assert citation['url'] == value['results'][0]['url']
    assert citation['providers'] == ['duckduckgo', 'brave']
    assert citation['assertion_status'] == 'source_assertion_unvalidated'
    assert citation['target_status'] == 'unvisited_search_result'
    fetched = call(system, 'lane_fetch', {'lane_id': 'research', 'snapshot_id': first['snapshot_id'],
        'path': first['logical_name'], 'representation': 'original_source', 'max_bytes': 65536})
    assert fetched.status == 'ok', fetched.error
    assert base64.b64decode(fetched.result['result']['read']['result']['content_base64']) == raw
    assert fetched.result['result']['read']['result']['fidelity'] == 'provider_query_response_bundle_not_target_documents'
    local = execute(system, actions[1], {'filename': 'note.md'}, index=1)
    search = call(system, 'lane_search', {'lane_id': 'research', 'query': 'SQLite'})
    assert search.status == 'ok', search.error
    assert {row['snapshot_id'] for row in search.result['arms'][0]['queries']} == {first['snapshot_id'], local['snapshot_id']}
    second = execute(system, actions[2], {'query': 'SQLite', 'backends': ['duckduckgo', 'brave'],
        'expected_snapshot': first['snapshot_id']}, index=2)
    assert second['generation'] == 2 and second['previous_snapshot'] == first['snapshot_id']
    refreshed_citation = call(system, 'research_query', {'snapshot_id': second['snapshot_id'], 'collection': 'research_citation'})
    assert refreshed_citation.status == 'ok', refreshed_citation.error
    assert refreshed_citation.result['result']['rows'][0]['citation_id'] == expected_citation
    assert read_snapshot(system[1], first['snapshot_id'])[3] == raw
    with system[1].lane('research').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM research_discovery_version').fetchone()[0] == 2
        assert connection.execute('SELECT count(*) FROM research_web_version').fetchone()[0] == 0


def test_partial_provider_snapshot_keeps_the_failure_visible(system):
    plan(system, 'research', ['research_web_discover'], tools=TOOLS)
    value = execute(system, 'research_web_discover', {'query': 'SQLite fixture_brave_failed', 'backends': ['duckduckgo', 'brave']})
    assert value['status'] == 'partial' and value['result_count'] == 1
    assert value['attempts'][1]['status'] == 'failed'


def test_all_providers_failed_does_not_publish_or_complete(system):
    plan(system, 'research', ['research_web_discover'], tools=TOOLS)
    value = execute(system, 'research_web_discover', {'query': 'fixture_all_failed', 'backends': ['duckduckgo', 'brave']}, expected='blocked')
    assert value['error_code'] == 'RESEARCH_DISCOVERY_WORKER_FAILED'
    assert call(system, 'research_current').result['result']['files'] == []


@pytest.mark.parametrize('system', ['no_network'], indirect=True)
def test_discovery_requires_live_network_authorization(system):
    plan(system, 'research', ['research_web_discover'], tools=TOOLS)
    value = execute(system, 'research_web_discover', {'query': 'SQLite'}, expected='blocked')
    assert value['error_code'] == 'PROJECT_NOT_SELECTED'
    assert system[0].workers.status()['succeeded_operations'] == 0


@pytest.mark.parametrize('key,value', [('status', 'complete'), ('target_documents_ingested', True), ('result_count', 20)])
def test_changed_discovery_claims_cannot_complete(system, monkeypatch, key, value):
    from evidence_lane_plugin import research_discovery_profile
    original = research_discovery_profile.result

    def changed(store, lane_id, operation, body):
        return original(store, lane_id, operation, {**body, key: value})

    monkeypatch.setattr(research_discovery_profile, 'result', changed)
    plan(system, 'research', ['research_web_discover'], tools=TOOLS)
    failed = execute(system, 'research_web_discover', {'query': 'SQLite fixture_brave_failed',
        'backends': ['duckduckgo', 'brave']}, expected='blocked')
    assert failed['error_code'] == 'DELTA_ACCEPTANCE_FAILED'


def test_changed_discovery_response_bytes_cannot_be_read(system):
    from evidence_lane_plugin.research_discovery_profile import read_snapshot
    plan(system, 'research', ['research_web_discover'], tools=TOOLS)
    first = execute(system, 'research_web_discover', {'query': 'SQLite'})
    manifest = read_snapshot(system[1], first['snapshot_id'])[0]
    path = system[1].lane('research').object_path(manifest['raw_object'])
    value = json.loads(path.read_bytes())
    value['results'][0]['url'] = 'https://changed.test/'
    path.write_bytes(json.dumps(value).encode())
    with pytest.raises(LaneError) as error:
        read_snapshot(system[1], first['snapshot_id'])
    assert error.value.code == 'SECTOR_EVIDENCE_OBJECT_INTEGRITY'


def test_actual_provider_transport_preserves_compressed_bytes_and_explicit_preferences(server, monkeypatch):
    from .test_research_web_v4 import HTML
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        monkeypatch.setenv(name, 'http://127.0.0.1:1')
    deadline, selected = budget(10, 16384, 5)
    transport = ProviderTransport(deadline=deadline, budget=selected, backend='fixture', roots=('127.0.0.1',))
    transport.set_cookies('127.0.0.1', {'safe': 'explicit'})
    first = transport.request('GET', server[0] + '/gzip')
    assert first.content == HTML
    second = transport.request('GET', server[0] + '/redirect')
    assert second.content == HTML and selected['requests'] == 3
    assert all(event['cookie'] == 'safe=explicit' and event['authorization'] is None for event in server[1])
    assert selected['events'][0]['wire_sha256'] != selected['events'][0]['body_sha256']


@pytest.mark.parametrize('path,code', [('/bomb', 'RESPONSE_BUDGET'), ('/ok', 'RESPONSE_BUDGET'),
    ('/loop', 'PROVIDER_REDIRECT'), ('/error', 'HTTP_STATUS')])
def test_actual_provider_transport_bounds_and_no_retry(server, path, code):
    deadline, selected = budget(10, 1024, 5)
    transport = ProviderTransport(deadline=deadline, budget=selected, backend='fixture', roots=('127.0.0.1',))
    with pytest.raises(ValueError, match='RESEARCH_DISCOVERY_' + code):
        transport.request('GET', server[0] + path)
    assert len(server[1]) == 1


def test_discovery_package_version_must_match_the_adapted_provider_contract(synthetic_provider, monkeypatch):
    import importlib.metadata
    original = importlib.metadata.version
    monkeypatch.setattr(importlib.metadata, 'version', lambda name: '9.15.0' if name == 'ddgs' else original(name))
    with pytest.raises(ValueError, match='RESEARCH_DISCOVERY_ADAPTER_VERSION'):
        discover('SQLite')
    assert not synthetic_provider


def test_failed_discovery_publication_rolls_back_current_selector(system, monkeypatch):
    from evidence_lane_plugin.storage import ProjectStore
    original = ProjectStore.append_receipt

    def failed(store, kind, body, **kwargs):
        if kind == 'research_discovery_snapshot':
            raise LaneError('FIXTURE_PUBLICATION_FAILED', 'Deliberate pre-publication failure')
        return original(store, kind, body, **kwargs)

    monkeypatch.setattr(ProjectStore, 'append_receipt', failed)
    plan(system, 'research', ['research_web_discover'], tools=TOOLS)
    execute(system, 'research_web_discover', {'query': 'SQLite'}, expected='blocked')
    assert call(system, 'research_current').result['result']['files'] == []


def test_discovery_current_selector_prevents_stale_queries_and_read_mutation(system):
    plan(system, 'research', ['research_web_discover', 'research_web_discover'], tools=TOOLS)
    first = execute(system, 'research_web_discover', {'query': 'SQLite'})
    calls = system[0].workers.status()['succeeded_operations']
    failed = execute(system, 'research_web_discover', {'query': 'SQLite'}, index=1, expected='blocked')
    assert failed['error_code'] == 'RESEARCH_DISCOVERY_SNAPSHOT_CHANGED'
    assert system[0].workers.status()['succeeded_operations'] == calls
    before = {path: path.read_bytes() for path in system[1].root.rglob('*.sqlite*') if path.is_file()}
    for action, arguments in [('research_current', {}), ('research_query', {'snapshot_id': first['snapshot_id'], 'query': 'SQLite'}),
        ('research_read', {'snapshot_id': first['snapshot_id']})]:
        response = call(system, action, arguments)
        assert response.status == 'ok', response.error
    assert before == {path: path.read_bytes() for path in system[1].root.rglob('*.sqlite*') if path.is_file()}
