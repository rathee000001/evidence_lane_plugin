"""Actual HTTP transports and owned Research publication, using bounded loopback fixtures."""
from __future__ import annotations

import base64
import gzip
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.research_web_content import EXTRACTORS, extract, safe_url
from evidence_lane_plugin.research_web_fetch import capture, pinned_public_destination
from evidence_lane_plugin.research_web_workers import web_worker_operations
from evidence_lane_plugin.sector_evidence_workers import evidence_worker_operations
from evidence_lane_plugin.workers import WorkerOperation, WorkerPool

from .test_evidence_sectors_v4 import call, execute, plan

HTML = ('<html><head><title>Recorded evidence</title><script>secretmarker</script></head><body><article><h1>Source observation</h1>'
    + ''.join(f'<p>Paragraph {i}: selected source evidence explains the bounded observation, its methods and results.</p>' for i in range(15))
    + '<p>Finding: this is an unvalidated assertion.</p><a href="/unvisited">Reference</a>'
    + '<embed src="/unvisited"><p>Content after a void element.</p></article></body></html>').encode()
TOOLS = ('Python', 'SQLite_FTS5_BM25', 'HTTPX', 'Requests', 'validators', 'tldextract', 'trafilatura',
    'readability_lxml', 'BeautifulSoup4', 'lxml', 'markdownify', 'html2text')


def capture_private_fixture(arguments):
    from evidence_lane_plugin.research_web_workers import capture_source
    return capture_source(arguments, _allow_non_public_for_tests=True)


def fixture_web_worker_operations():
    return (WorkerOperation('research_web_capture_source', __name__, 'capture_private_fixture',
        max_output_bytes=41_943_040), *web_worker_operations()[1:])


@pytest.fixture
def server():
    events = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            events.append({'path': self.path, 'cookie': self.headers.get('Cookie'),
                'authorization': self.headers.get('Authorization')})
            if self.path in {'/redirect', '/loop'}:
                self.send_response(302)
                self.send_header('Location', '/ok' if self.path == '/redirect' else '/loop')
                self.send_header('Set-Cookie', 'private=never-forwarded')
                self.end_headers()
                return
            if self.path == '/error':
                self.send_response(503)
                self.end_headers()
                return
            raw = gzip.compress(b'x' * 64000 if self.path == '/bomb' else HTML) if self.path in {'/gzip', '/bomb'} else HTML
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            if self.path in {'/gzip', '/bomb'}:
                self.send_header('Content-Encoding', 'gzip')
            self.end_headers()
            self.wfile.write(raw)

    instance = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{instance.server_port}', events
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(5)
        assert not thread.is_alive()


@pytest.fixture
def system(tmp_path, request):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'note.md').write_text('Evidence: independent local source evidence\n', encoding='utf-8')
    with Engine(tmp_path / 'runtime', worker_pool=WorkerPool((*fixture_web_worker_operations(), *evidence_worker_operations()), workers=1)) as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        permissions = ['read', 'write', 'tools', 'admin']
        if getattr(request, 'param', 'network') == 'network':
            permissions.append('network')
        _, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=store.project_id, permissions=permissions)]))
        yield engine, store, session


@pytest.mark.parametrize('extractor', EXTRACTORS)
def test_actual_extractors_preserve_source_and_do_not_follow_links(extractor):
    result = extract(HTML, extractor, source_url='https://example.test/source')
    assert result['status'] == 'extracted' and result['text']
    assert 'secretmarker' not in result['text']
    assert result['links'] == [{'url': 'https://example.test/unvisited', 'line': 1, 'status': 'unvisited_source_link'}]
    assert not result['network_used'] and not result['scripts_executed']
    assert not result['source_assertions_validated']
    assert bool(result['versions']) is (extractor != 'stdlib')
    if extractor == 'stdlib':
        assert 'Content after a void element.' in result['text']


@pytest.mark.parametrize('url', ['file:///secret', 'https://a.test\\b', 'https://user:pass@a.test',
    'http://a.test:65536/', 'http://a.test/%0a\n', 'http://%61.test/', 'javascript:alert(1)'])
def test_invalid_urls_are_rejected_before_network(url):
    with pytest.raises(ValueError):
        safe_url(url)


@pytest.mark.parametrize('url', ['http://127.0.0.1/', 'http://[::1]/',
    'http://169.254.169.254/latest/meta-data/', 'http://10.0.0.1/'])
def test_non_public_destinations_are_rejected_before_http(url, monkeypatch):
    monkeypatch.setattr('evidence_lane_plugin.research_web_fetch.response_stream',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('HTTP must not start')))
    with pytest.raises(ValueError, match='RESEARCH_WEB_DESTINATION_DENIED'):
        capture(url, 'httpx')


def test_destination_resolution_is_pinned_for_the_connection(monkeypatch):
    import socket
    calls = []
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
    private = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
    def resolver(*args, **kwargs):
        calls.append((args, kwargs))
        return public if len(calls) == 1 else private
    monkeypatch.setattr(socket, 'getaddrinfo', resolver)
    with pinned_public_destination('https://example.com/') as receipt:
        assert socket.getaddrinfo('example.com', 443) == public
        assert receipt['dns_rebinding_prevented'] and receipt['address_count'] == 1
    assert len(calls) == 1


@pytest.mark.parametrize('transport', ['httpx', 'requests'])
def test_transports_preserve_wire_bytes_and_ignore_ambient_proxy_and_cookies(server, monkeypatch, transport):
    url, events = server
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        monkeypatch.setenv(name, 'http://127.0.0.1:1')
    first = capture(url + '/gzip', transport, max_bytes=4096,
        _allow_non_public_for_tests=True)
    assert base64.b64decode(first['body_base64']) == HTML
    assert gzip.decompress(base64.b64decode(first['wire_base64'])) == HTML
    second = capture(url + '/redirect', transport, max_bytes=4096,
        _allow_non_public_for_tests=True)
    assert second['final_url'] == url + '/ok' and len(second['redirects']) == 1
    assert len(events) == 3 and all(event['cookie'] is None and event['authorization'] is None for event in events)


@pytest.mark.parametrize('transport', ['httpx', 'requests'])
@pytest.mark.parametrize('path,code', [('/bomb', 'DECODED_BUDGET'), ('/loop', 'REDIRECT_LOOP'),
    ('/error', 'HTTP_STATUS'), ('/ok', 'RESPONSE_BUDGET')])
def test_transport_bounds_do_not_retry(server, transport, path, code):
    url, events = server
    with pytest.raises(ValueError, match='RESEARCH_WEB_' + code):
        capture(url + path, transport, max_bytes=1024,
            _allow_non_public_for_tests=True)
    assert len(events) == 1


@pytest.mark.parametrize('transport', ['httpx', 'requests'])
def test_owned_capture_offline_extraction_refresh_and_unified_local_search(system, server, transport):
    url, events = server
    actions = ['research_web_capture', 'research_web_extract', 'research_index', 'research_web_capture']
    plan(system, 'research', actions, tools=TOOLS)
    captured = execute(system, actions[0], {'url': url + '/gzip', 'transport': transport})
    assert len(events) == 1
    assert base64.b64decode(call(system, 'research_read', {'snapshot_id': captured['snapshot_id']}).result['result']['content_base64']) == HTML
    extracted = execute(system, actions[1], {'snapshot_id': captured['snapshot_id'],
        'expected_snapshot': captured['snapshot_id'], 'extractor': 'trafilatura'}, index=1)
    assert len(events) == 1 and extracted['capture_snapshot'] == captured['snapshot_id']
    assert extracted['sha256'] == captured['sha256'] and extracted['generation'] == 2
    local = execute(system, actions[2], {'filename': 'note.md'}, index=2)
    current = call(system, 'research_current').result['result']['files']
    assert {row['snapshot_id'] for row in current} == {extracted['snapshot_id'], local['snapshot_id']}
    search = call(system, 'lane_search', {'lane_id': 'research', 'query': 'source evidence'})
    assert search.status == 'ok', search.error
    assert search.result['arms'][0]['state'] == 'hit'
    queries = search.result['arms'][0]['queries']
    assert len(queries) == 2, queries
    assert {query['snapshot_id'] for query in queries} == {extracted['snapshot_id'], local['snapshot_id']}
    fetched = call(system, 'lane_fetch', {'lane_id': 'research', 'snapshot_id': captured['snapshot_id'],
        'path': captured['logical_name'], 'representation': 'original_source', 'max_bytes': 4096})
    assert fetched.status == 'ok', fetched.error
    assert base64.b64decode(fetched.result['result']['read']['result']['content_base64']) == HTML
    refreshed = execute(system, actions[3], {'url': url + '/gzip', 'transport': transport,
        'expected_snapshot': extracted['snapshot_id']}, index=3)
    assert len(events) == 2 and refreshed['generation'] == 3
    assert base64.b64decode(call(system, 'research_read', {'snapshot_id': captured['snapshot_id']}).result['result']['content_base64']) == HTML
    with system[1].lane('research').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM research_web_version').fetchone()[0] == 3
        assert connection.execute('SELECT count(*) FROM research_version').fetchone()[0] == 1
    assert (system[1].source_root / 'note.md').read_text() == 'Evidence: independent local source evidence\n'


@pytest.mark.parametrize('system', ['no_network'], indirect=True)
def test_missing_network_grant_blocks_before_worker_or_publication(system, server):
    url, events = server
    plan(system, 'research', ['research_web_capture'], tools=TOOLS)
    blocked = execute(system, 'research_web_capture', {'url': url + '/ok'}, expected='blocked')
    assert blocked['error_code'] == 'PROJECT_NOT_SELECTED'
    assert not events and system[0].workers.status()['succeeded_operations'] == 0
    assert call(system, 'research_current').result['result']['files'] == []


def test_failed_http_response_does_not_publish_a_current_snapshot(system, server):
    url, events = server
    plan(system, 'research', ['research_web_capture'], tools=TOOLS)
    blocked = execute(system, 'research_web_capture', {'url': url + '/error'}, expected='blocked')
    assert blocked['error_code'] == 'RESEARCH_WEB_WORKER_FAILED'
    assert len(events) == 1
    assert call(system, 'research_current').result['result']['files'] == []


def test_web_snapshot_query_checks_typed_payload_and_retains_unvalidated_labels(system, server):
    from evidence_lane_plugin.errors import LaneError
    from evidence_lane_plugin.research_web_profile import read_snapshot
    url, _ = server
    plan(system, 'research', ['research_web_capture'], tools=TOOLS)
    captured = execute(system, 'research_web_capture', {'url': url + '/ok'})
    findings = call(system, 'research_query', {'snapshot_id': captured['snapshot_id'], 'collection': 'research_finding'})
    assert findings.status == 'ok', findings.error
    rows = findings.result['result']['rows']
    assert rows and all(row['assertion_status'] == 'source_assertion_unvalidated' for row in rows)
    manifest = read_snapshot(system[1], captured['snapshot_id'])[0]
    path = system[1].lane('research').object_path(manifest['body_object'])
    path.write_bytes(b'changed')
    with pytest.raises(LaneError) as error:
        read_snapshot(system[1], captured['snapshot_id'])
    assert error.value.code == 'SECTOR_EVIDENCE_OBJECT_INTEGRITY'


@pytest.mark.parametrize('key,value', [('generation', 999), ('bytes', 0), ('url', 'https://wrong.test/'),
    ('source_assertions_validated', True), ('remote_currentness', 'live_rechecked'), ('extra_claim', 'verified')])
def test_modified_web_output_cannot_complete_delta(system, server, monkeypatch, key, value):
    from evidence_lane_plugin import research_web_profile
    original = research_web_profile.result

    def changed(store, lane_id, operation, body):
        return original(store, lane_id, operation, {**body, key: value})

    monkeypatch.setattr(research_web_profile, 'result', changed)
    plan(system, 'research', ['research_web_capture'], tools=TOOLS)
    blocked = execute(system, 'research_web_capture', {'url': server[0] + '/ok'}, expected='blocked')
    assert blocked['error_code'] == 'DELTA_ACCEPTANCE_FAILED'


def test_network_authorization_is_rechecked_after_actual_capture(system, server, monkeypatch):
    from evidence_lane_plugin import research_web_profile
    from evidence_lane_plugin.errors import LaneError
    original = research_web_profile.network_check

    def changed(context):
        original(context)
        if server[1]:
            raise LaneError('PERMISSION_DENIED', 'Fixture revokes network at the post-request boundary')

    monkeypatch.setattr(research_web_profile, 'network_check', changed)
    plan(system, 'research', ['research_web_capture'], tools=TOOLS)
    blocked = execute(system, 'research_web_capture', {'url': server[0] + '/ok'}, expected='blocked')
    assert blocked['error_code'] == 'PERMISSION_DENIED' and len(server[1]) == 1
    assert call(system, 'research_current').result['result']['files'] == []


def test_receipt_failure_rolls_back_web_selectors_with_lane_publication(system, server, monkeypatch):
    from evidence_lane_plugin.errors import LaneError
    from evidence_lane_plugin.storage import ProjectStore
    original = ProjectStore.append_receipt

    def failed(store, kind, body, **kwargs):
        if kind == 'research_web_snapshot':
            raise LaneError('FIXTURE_PUBLICATION_FAILED', 'Fail before the coordinated publication')
        return original(store, kind, body, **kwargs)

    monkeypatch.setattr(ProjectStore, 'append_receipt', failed)
    plan(system, 'research', ['research_web_capture'], tools=TOOLS)
    execute(system, 'research_web_capture', {'url': server[0] + '/ok'}, expected='blocked')
    assert call(system, 'research_current').result['result']['files'] == []


def test_current_selector_rejects_stale_capture_before_network(system, server):
    url, events = server
    plan(system, 'research', ['research_web_capture', 'research_web_capture'], tools=TOOLS)
    first = execute(system, 'research_web_capture', {'url': url + '/ok'})
    blocked = execute(system, 'research_web_capture', {'url': url + '/ok'}, index=1, expected='blocked')
    assert blocked['error_code'] == 'RESEARCH_WEB_SNAPSHOT_CHANGED' and len(events) == 1
    assert call(system, 'research_current').result['result']['files'][0]['snapshot_id'] == first['snapshot_id']


def test_preinvocation_requests_selection_and_read_only_byte_stability(system, server, monkeypatch):
    router = system[0].registry.tool_router
    original = router.observer

    def observed(identity, context):
        value = original(identity, context)
        return {**value, 'ready': False, 'reason': 'FIXTURE_PACKAGE_UNAVAILABLE'} if identity == 'HTTPX' else value

    monkeypatch.setattr(router, 'observer', observed)
    plan(system, 'research', ['research_web_capture'], tools=TOOLS)
    captured = execute(system, 'research_web_capture', {'url': server[0] + '/ok'})
    from evidence_lane_plugin.research_web_profile import read_snapshot
    manifest = read_snapshot(system[1], captured['snapshot_id'])[0]
    assert manifest['capture']['transport'] == 'requests' and len(server[1]) == 1
    before = {path: path.read_bytes() for path in system[1].root.rglob('*.sqlite*') if path.is_file()}
    for action, arguments in [('research_current', {}), ('research_query', {'snapshot_id': captured['snapshot_id'], 'query': 'source'}),
        ('research_read', {'snapshot_id': captured['snapshot_id']})]:
        response = call(system, action, arguments)
        assert response.status == 'ok', response.error
    assert before == {path: path.read_bytes() for path in system[1].root.rglob('*.sqlite*') if path.is_file()}
    assert len(server[1]) == 1


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Studio service targets Windows only')
def test_service_worker_rejects_non_public_destination(tmp_path, server):
    from evidence_lane_plugin.research_web_contracts import Capture
    from evidence_lane_plugin.service import Service
    service = Service(tmp_path / 'runtime', workers=1)
    with service.engine:
        response = service.engine.workers.submit('research_web_capture_source', {
            **Capture(url=server[0] + '/ok').model_dump(mode='json'), 'selected_transport': 'httpx'}).result()
        assert response == {'status': 'error', 'code': 'WORKER_OPERATION_FAILED'}
        assert server[1] == []


def test_auto_extraction_selects_ready_algorithm_before_invocation(system, server, monkeypatch):
    router = system[0].registry.tool_router
    observed = router.observer

    def unavailable(identity, context):
        value = observed(identity, context)
        return {**value, 'ready': False, 'reason': 'FIXTURE_PACKAGE_UNAVAILABLE'} if identity == 'trafilatura' else value

    monkeypatch.setattr(router, 'observer', unavailable)
    plan(system, 'research', ['research_web_capture', 'research_web_extract'], tools=TOOLS)
    captured = execute(system, 'research_web_capture', {'url': server[0] + '/ok'})
    extracted = execute(system, 'research_web_extract', {'snapshot_id': captured['snapshot_id'],
        'expected_snapshot': captured['snapshot_id']}, index=1)
    assert extracted['parser'] == 'readability'
    assert system[0].workers.status()['succeeded_operations'] == 2 and len(server[1]) == 1


def test_selected_extractor_failure_does_not_invoke_another_algorithm(system, server, monkeypatch):
    from concurrent.futures import Future
    plan(system, 'research', ['research_web_capture', 'research_web_extract'], tools=TOOLS)
    captured = execute(system, 'research_web_capture', {'url': server[0] + '/ok'})
    submit = system[0].workers.submit
    selections = []

    def failed(operation, arguments, **kwargs):
        response = submit(operation, arguments, **kwargs).result()
        if operation == 'research_web_extract_source':
            selections.append(arguments['extractor'])
            response = {'status': 'error', 'code': 'WORKER_OPERATION_FAILED'}
        future = Future()
        future.set_result(response)
        return future

    monkeypatch.setattr(system[0].workers, 'submit', failed)
    blocked = execute(system, 'research_web_extract', {'snapshot_id': captured['snapshot_id'],
        'expected_snapshot': captured['snapshot_id']}, index=1, expected='blocked')
    assert blocked['error_code'] == 'RESEARCH_WEB_WORKER_FAILED' and selections == ['trafilatura']
    assert call(system, 'research_current').result['result']['files'][0]['snapshot_id'] == captured['snapshot_id']
