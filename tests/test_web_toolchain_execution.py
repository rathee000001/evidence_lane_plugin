from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from evidence_lane_plugin.web_toolchain import (
    WebDiscoveryRequest,
    WebFetchRequest,
    discover_web_sources,
    extract_web_document,
    fetch_web_document,
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = (
            b"<html><head><title>Evidence</title><script>ignore()</script></head>"
            b"<body><article><h1>Lane</h1><p>Preserve exact source truth.</p>"
            b"</article></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def test_offline_html_extraction_runs_ordered_stack() -> None:
    receipt = extract_web_document(
        "<html><body><main><h1>Evidence Lane</h1><p>Exact truth.</p></main></body></html>"
    )
    assert receipt["status"] == "PASS"
    assert "Evidence Lane" in receipt["text"]
    assert receipt["network_used"] is False
    assert {row["tool"] for row in receipt["attempts"]} >= {
        "trafilatura",
        "beautifulsoup4+lxml",
        "markdownify",
        "html2text",
    }


def test_web_routes_require_explicit_network_grant() -> None:
    with pytest.raises(ValueError, match="EXPLICIT_NETWORK_GRANT_REQUIRED"):
        fetch_web_document(
            WebFetchRequest(
                url="https://example.com",
                host_profile="CODEX_DESKTOP",
            )
        )
    with pytest.raises(ValueError, match="EXPLICIT_NETWORK_GRANT_REQUIRED"):
        discover_web_sources(
            WebDiscoveryRequest(
                query="evidence lane",
                host_profile="CODEX_CLI",
            )
        )


def test_httpx_primary_and_requests_fallback_are_bounded(monkeypatch) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    try:
        request = WebFetchRequest(
            url=url,
            network_allowed=True,
            host_profile="CODEX_DESKTOP",
        )
        primary = fetch_web_document(request)
        assert primary["fetcher"] == "HTTPX"
        assert primary["extraction"]["status"] == "PASS"
        assert primary["source_mutated"] is False

        import httpx

        def _fail(*_args, **_kwargs):
            raise httpx.TransportError("bounded test fallback")

        monkeypatch.setattr(httpx, "get", _fail)
        fallback = fetch_web_document(request)
        assert fallback["fetcher"] == "REQUESTS_FALLBACK"
        assert fallback["primary_error"] == "TransportError"
        assert fallback["extraction"]["status"] == "PASS"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
