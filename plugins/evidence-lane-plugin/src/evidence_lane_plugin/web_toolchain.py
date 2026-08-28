"""Bounded web/source extraction and explicit-network research toolchain."""

from __future__ import annotations

import importlib.util
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .hashing import canonical_json_bytes, sha256_bytes


class WebFetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: HttpUrl
    network_allowed: bool = False
    timeout_seconds: int = Field(default=20, ge=1, le=60)
    max_bytes: int = Field(default=8_000_000, ge=1_024, le=32_000_000)
    host_profile: str


class WebDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=500)
    network_allowed: bool = False
    limit: int = Field(default=10, ge=1, le=50)
    host_profile: str


def extract_web_document(
    html: str,
    *,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Run the ordered offline HTML extraction stack over supplied bytes."""

    source = str(html or "")
    attempts: list[dict[str, Any]] = []
    selected = "NONE"
    text = ""
    markdown = ""

    if importlib.util.find_spec("trafilatura") is not None:
        import trafilatura  # type: ignore[import-not-found]

        extracted = trafilatura.extract(
            source,
            url=source_url,
            include_comments=False,
            include_tables=True,
            output_format="markdown",
        )
        attempts.append({"tool": "trafilatura", "chars": len(extracted or "")})
        if extracted:
            markdown = str(extracted)
            text = markdown
            selected = "TRAFILATURA"
    else:
        attempts.append({"tool": "trafilatura", "available": False, "chars": 0})

    if not text and importlib.util.find_spec("readability") is not None:
        from readability import Document  # type: ignore[import-not-found]

        summary = Document(source).summary(html_partial=True)
        attempts.append({"tool": "readability-lxml", "chars": len(summary)})
        if summary:
            source = summary
            selected = "READABILITY_LXML"
    elif importlib.util.find_spec("readability") is None:
        attempts.append({"tool": "readability-lxml", "available": False, "chars": 0})

    if importlib.util.find_spec("bs4") is not None:
        from bs4 import BeautifulSoup  # type: ignore[import-not-found]

        soup = BeautifulSoup(source, "lxml")
        for node in soup(["script", "style", "noscript", "template"]):
            node.decompose()
        cleaned_html = str(soup)
        soup_text = "\n".join(
            line.strip() for line in soup.get_text("\n").splitlines() if line.strip()
        )
        attempts.append({"tool": "beautifulsoup4+lxml", "chars": len(soup_text)})
        if not text and soup_text:
            text = soup_text
            selected = "BEAUTIFULSOUP4_LXML"
    else:
        cleaned_html = source
        attempts.append({"tool": "beautifulsoup4+lxml", "available": False, "chars": 0})

    if importlib.util.find_spec("markdownify") is not None:
        from markdownify import markdownify  # type: ignore[import-not-found]

        projected = markdownify(cleaned_html, heading_style="ATX")
        attempts.append({"tool": "markdownify", "chars": len(projected)})
        if not markdown and projected:
            markdown = projected
    else:
        attempts.append({"tool": "markdownify", "available": False, "chars": 0})
    if not text and markdown:
        text = markdown
        selected = "MARKDOWNIFY"

    if importlib.util.find_spec("html2text") is not None:
        import html2text  # type: ignore[import-not-found]

        secondary = html2text.html2text(cleaned_html)
        attempts.append({"tool": "html2text", "chars": len(secondary)})
        if not text and secondary:
            text = secondary
            selected = "HTML2TEXT"
    else:
        attempts.append({"tool": "html2text", "available": False, "chars": 0})

    if not text:
        class _TextExtractor(HTMLParser):
            def __init__(self) -> None:
                super().__init__(convert_charrefs=True)
                self.parts: list[str] = []
                self.suppressed = 0

            def handle_starttag(
                self, tag: str, attrs: list[tuple[str, str | None]]
            ) -> None:
                del attrs
                if tag.casefold() in {"script", "style", "noscript", "template"}:
                    self.suppressed += 1

            def handle_endtag(self, tag: str) -> None:
                if (
                    tag.casefold() in {"script", "style", "noscript", "template"}
                    and self.suppressed
                ):
                    self.suppressed -= 1

            def handle_data(self, data: str) -> None:
                if not self.suppressed and data.strip():
                    self.parts.append(data.strip())

        parser = _TextExtractor()
        parser.feed(source)
        fallback_text = "\n".join(parser.parts)
        attempts.append({"tool": "stdlib-htmlparser", "chars": len(fallback_text)})
        if fallback_text:
            text = fallback_text
            selected = "STDLIB_HTMLPARSER"

    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    core = {
        "schema": "evidence-lane.web-extraction.v1",
        "status": "PASS" if normalized else "EMPTY",
        "selected_tool": selected,
        "attempts": attempts,
        "source_url": source_url,
        "text": normalized,
        "markdown": markdown.strip(),
        "text_sha256": sha256_bytes(normalized.encode("utf-8")),
        "network_used": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def fetch_web_document(request: WebFetchRequest) -> dict[str, Any]:
    """Fetch only under an explicit network grant; HTTPX primary, Requests fallback."""

    if not request.network_allowed:
        raise ValueError("WEB_FETCH_EXPLICIT_NETWORK_GRANT_REQUIRED")
    if request.host_profile.strip().upper() not in {
        "CODEX_DESKTOP",
        "CODEX_CLI",
        "CODEX_VM",
    }:
        raise ValueError("WEB_FETCH_CODEX_HOST_PROFILE_REQUIRED")
    url = str(request.url)
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("WEB_FETCH_URL_INVALID")
    if importlib.util.find_spec("validators") is not None:
        import validators  # type: ignore[import-not-found]

        if validators.url(url) is not True:
            raise ValueError("WEB_FETCH_URL_INVALID")
    if importlib.util.find_spec("tldextract") is not None:
        import tldextract  # type: ignore[import-not-found]

        domain = tldextract.TLDExtract(suffix_list_urls=())(
            url
        ).top_domain_under_public_suffix
    else:
        domain = str(parsed.hostname)
    headers = {"User-Agent": "EvidenceLane/3.0 governed-source-intake"}
    fetcher = "HTTPX"
    try:
        import httpx
        from tenacity import (
            Retrying,
            retry_if_exception_type,
            stop_after_attempt,
            wait_fixed,
        )

        response = None
        for attempt in Retrying(
            stop=stop_after_attempt(2),
            wait=wait_fixed(0.1),
            retry=retry_if_exception_type(httpx.TransportError),
            reraise=True,
        ):
            with attempt:
                response = httpx.get(
                    url,
                    headers=headers,
                    follow_redirects=True,
                    timeout=request.timeout_seconds,
                )
        assert response is not None
        response.raise_for_status()
        raw = response.content
        final_url = str(response.url)
        content_type = str(response.headers.get("content-type") or "")
    except Exception as primary_error:  # noqa: BLE001 - ordered fallback evidence
        import requests  # type: ignore[import-not-found]

        fetcher = "REQUESTS_FALLBACK"
        response = requests.get(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=request.timeout_seconds,
        )
        response.raise_for_status()
        raw = bytes(response.content)
        final_url = str(response.url)
        content_type = str(response.headers.get("content-type") or "")
        primary_error_name = type(primary_error).__name__
    if len(raw) > request.max_bytes:
        raise ValueError("WEB_FETCH_RESPONSE_BOUND_EXCEEDED")
    decoded = raw.decode("utf-8", errors="replace")
    extraction = extract_web_document(decoded, source_url=final_url)
    core = {
        "schema": "evidence-lane.web-fetch.v1",
        "status": "PASS",
        "fetcher": fetcher,
        "primary_error": (
            primary_error_name if fetcher == "REQUESTS_FALLBACK" else None
        ),
        "requested_url_sha256": sha256_bytes(url.encode("utf-8")),
        "final_url_sha256": sha256_bytes(final_url.encode("utf-8")),
        "registrable_domain": domain,
        "content_type": content_type,
        "response_bytes": len(raw),
        "response_sha256": sha256_bytes(raw),
        "extraction": extraction,
        "network_grant": True,
        "source_mutated": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def discover_web_sources(request: WebDiscoveryRequest) -> dict[str, Any]:
    """Run DDGS only under an explicit research/source-intake network grant."""

    if not request.network_allowed:
        raise ValueError("WEB_DISCOVERY_EXPLICIT_NETWORK_GRANT_REQUIRED")
    if request.host_profile.strip().upper() not in {
        "CODEX_DESKTOP",
        "CODEX_CLI",
        "CODEX_VM",
    }:
        raise ValueError("WEB_DISCOVERY_CODEX_HOST_PROFILE_REQUIRED")
    from ddgs import DDGS  # type: ignore[import-not-found]

    rows = list(DDGS().text(request.query, max_results=request.limit))
    results = [
        {
            "title": str(row.get("title") or ""),
            "url": str(row.get("href") or row.get("url") or ""),
            "snippet": str(row.get("body") or row.get("snippet") or ""),
            "citation_id": "websrc_"
            + sha256_bytes(
                canonical_json_bytes(
                    {
                        "title": str(row.get("title") or ""),
                        "url": str(row.get("href") or row.get("url") or ""),
                        "snippet": str(row.get("body") or row.get("snippet") or ""),
                    }
                )
            )[:24].lower(),
        }
        for row in rows[: request.limit]
    ]
    core = {
        "schema": "evidence-lane.web-discovery.v1",
        "status": "PASS",
        "engine": "DDGS",
        "query_sha256": sha256_bytes(request.query.encode("utf-8")),
        "result_count": len(results),
        "results": results,
        "network_grant": True,
        "automatic_ingestion": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


__all__ = [
    "WebDiscoveryRequest",
    "WebFetchRequest",
    "discover_web_sources",
    "extract_web_document",
    "fetch_web_document",
]
