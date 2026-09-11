"""Synthetic provider responses used only by local discovery qualification."""
import json
from urllib.parse import parse_qs

import httpx

FIXTURES = {
    'brave': '<div data-type="web"><a href="https://example.test/sqlite"><div class="title">SQLite fixture evidence</div></a><div class="snippet"><div class="content">SQLite source documentation</div></div></div>',
    'duckduckgo': '<div class="body"><h2>SQLite fixture evidence</h2><a href="https://example.test/sqlite">SQLite source documentation</a></div>',
    'google': '<?><div><div><a href="https://example.test/sqlite"><span>SQLite fixture evidence</span></a></div><div><table><tr><td>SQLite source documentation</td></tr></table></div></div>',
    'mojeek': '<ul class="results"><li><h2><a href="https://example.test/sqlite">SQLite fixture evidence</a></h2><p class="s">SQLite source documentation</p></li></ul>',
    'startpage': '<div class="result"><a href="https://example.test/sqlite"><h2>SQLite fixture evidence</h2></a><p>SQLite source documentation</p></div>',
    'yahoo': '<div class="relsrch"><div class="Title"><h3>SQLite fixture evidence</h3><a href="https://example.test/sqlite">SQLite</a></div><div class="Text">SQLite source documentation</div></div>',
}


def provider_response(request):
    host = request.url.host
    backend = next(name for name in (*FIXTURES, 'wikipedia') if host == name + '.com'
        or host.endswith(('.' + name + '.com', '.' + name + '.org')))
    query_and_body = str(request.url) + request.content.decode()
    if 'fixture_all_failed' in query_and_body or ('fixture_brave_failed' in query_and_body and backend == 'brave'):
        return httpx.Response(403, stream=httpx.ByteStream(b'provider fixture denied'))
    if backend == 'wikipedia':
        query = parse_qs(request.url.query.decode())
        payload = ['SQLite', ['SQLite fixture evidence'], [''], ['https://example.test/sqlite']] if query['action'] == ['opensearch'] else {
            'query': {'pages': {'1': {'extract': 'SQLite source documentation'}}}}
        raw = json.dumps(payload).encode()
    elif backend == 'startpage' and request.method == 'GET':
        raw = b'<form id="search"><input name="sc" value="fixture"/></form>'
    else:
        raw = FIXTURES[backend].encode()
    return httpx.Response(200, stream=httpx.ByteStream(raw))


def fixture_worker(arguments):
    from evidence_lane_plugin.research_discovery_workers import discover_source
    original = httpx.Client

    def client(**kwargs):
        return original(transport=httpx.MockTransport(provider_response), **kwargs)

    httpx.Client = client
    try:
        return discover_source(arguments)
    finally:
        httpx.Client = original
