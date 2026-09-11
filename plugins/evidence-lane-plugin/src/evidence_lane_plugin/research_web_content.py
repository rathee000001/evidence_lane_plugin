"""Offline adaptation of the original web extraction stack."""
from __future__ import annotations

import hashlib
import importlib.metadata
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_INPUT = 8_388_608
MAX_TEXT = 2_097_152
EXTRACTORS = ('trafilatura', 'readability', 'beautifulsoup', 'markdownify', 'html2text', 'stdlib')
DISTRIBUTIONS = {'trafilatura': ('trafilatura', 'beautifulsoup4', 'lxml'),
    'readability': ('readability-lxml', 'beautifulsoup4', 'lxml'),
    'beautifulsoup': ('beautifulsoup4', 'lxml'), 'markdownify': ('markdownify', 'beautifulsoup4', 'lxml'),
    'html2text': ('html2text', 'beautifulsoup4', 'lxml'), 'stdlib': ()}
SUPPRESSED = {'script', 'style', 'noscript', 'template', 'iframe', 'object', 'embed'}


def sha(content):
    return hashlib.sha256(content).hexdigest()


def safe_url(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 8192
            or any(ord(char) < 33 or ord(char) == 127 for char in value) or '\\' in value):
        raise ValueError('RESEARCH_WEB_URL_INVALID')
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or '%' in parsed.hostname):
        raise ValueError('RESEARCH_WEB_URL_INVALID')
    _ = parsed.port  # Validate a bounded numeric port before opening a connection.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or '/', parsed.query, ''))


class SourceHTML(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url, self.parts, self.links = base_url, [], []
        self.hidden, self.nodes, self.text_bytes = [], 0, 0
        self.links_truncated = False
        self.base_seen, self.pending_links, self.links_omitted = False, [], 0
        self.base_basis = 'response_url' if base_url else 'source_url_unavailable'

    def node(self):
        self.nodes += 1
        if self.nodes > 100_000:
            raise ValueError('RESEARCH_WEB_HTML_NODE_BUDGET')

    def handle_starttag(self, tag, attrs):
        self.node()
        if tag in SUPPRESSED and tag != 'embed':
            self.hidden.append(tag)
        if self.hidden:
            return
        # HTML uses the first base element with href, including an empty href.
        # Keep only our supported static HTTP(S) subset. An unsupported first
        # base must not silently redirect relative citations to the page URL.
        attributes = {}
        for key, value in attrs:
            attributes.setdefault(key, value)
        if tag == 'base' and 'href' in attributes and not self.base_seen:
            self.base_seen = True
            try:
                self.base_url = safe_url(urljoin(self.base_url or '', attributes['href'] or ''))
                self.base_basis = 'first_base_href'
            except ValueError:
                self.base_url, self.base_basis = None, 'unsupported_first_base_href'
        if tag == 'a' and 'href' in attributes:
            value = attributes['href'] or ''
            if len(value) > 8192:
                self.links_omitted += 1
            elif len(self.pending_links) < 2000:
                self.pending_links.append((value, self.getpos()[0]))
            else:
                self.links_truncated = True

    def close(self):
        super().close()
        # Resolve after the complete bounded scan: an earlier anchor also uses
        # the document's first base. No base or citation destination is fetched.
        self.links = []
        for value, line in self.pending_links:
            try:
                url = safe_url(urljoin(self.base_url or '', value))
            except ValueError:
                self.links_omitted += 1
                continue
            self.links.append({'url': url, 'line': line, 'status': 'unvisited_source_link'})
        self.pending_links.clear()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        self.node()
        if tag in self.hidden:
            index = len(self.hidden) - 1 - self.hidden[::-1].index(tag)
            del self.hidden[index:]

    def handle_data(self, value):
        self.node()
        if not self.hidden and value.strip():
            self.text_bytes += len(value.encode())
            if self.text_bytes > MAX_TEXT:
                raise ValueError('RESEARCH_WEB_TEXT_BUDGET')
            self.parts.append(value.strip())


def extract(content, extractor, *, source_url=None, encoding='utf-8'):
    if len(content) > MAX_INPUT or extractor not in EXTRACTORS:
        raise ValueError('RESEARCH_WEB_EXTRACTION_BOUND')
    if source_url is not None:
        source_url = safe_url(source_url)
    if encoding not in {'utf-8', 'utf-8-sig', 'windows-1252', 'iso-8859-1', 'utf-16'}:
        raise ValueError('RESEARCH_WEB_ENCODING_UNSUPPORTED')
    source = content.decode(encoding, errors='strict')
    probe = SourceHTML(source_url)
    probe.feed(source)
    probe.close()
    markdown, text = '', ''
    versions = {name: importlib.metadata.version(name) for name in DISTRIBUTIONS[extractor]}
    if extractor == 'stdlib':
        text = '\n'.join(probe.parts)
    else:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(source, 'lxml')
        for node in soup(list(SUPPRESSED)):
            node.decompose()
        cleaned = str(soup)
        if extractor == 'trafilatura':
            import trafilatura
            markdown = trafilatura.extract(cleaned, url=source_url, include_comments=False,
                include_tables=True, output_format='markdown', fast=True) or ''
            text = markdown
        elif extractor == 'readability':
            from readability import Document
            summary = Document(cleaned).summary(html_partial=True)
            text = BeautifulSoup(summary, 'lxml').get_text('\n')
        elif extractor == 'beautifulsoup':
            text = soup.get_text('\n')
        elif extractor == 'markdownify':
            from markdownify import markdownify
            text = markdown = markdownify(cleaned, heading_style='ATX')
        elif extractor == 'html2text':
            import html2text
            text = markdown = html2text.html2text(cleaned)
    text = '\n'.join(line.rstrip() for line in text.splitlines() if line.strip()).strip()
    markdown = markdown.strip()
    if max(len(text.encode()), len(markdown.encode())) > MAX_TEXT:
        raise ValueError('RESEARCH_WEB_TEXT_BUDGET')
    return {'schema': 'evidence-lane.research-web-extraction.v4', 'extractor': extractor,
        'status': 'extracted' if text else 'empty', 'text': text, 'markdown': markdown,
        'text_sha256': sha(text.encode()), 'source_sha256': sha(content), 'source_url': source_url,
        'encoding': encoding, 'versions': versions, 'links': probe.links, 'links_truncated': probe.links_truncated,
        'citation_resolution': {'base_url': probe.base_url, 'basis': probe.base_basis,
            'omitted_links': probe.links_omitted, 'parser': 'bounded_static_htmlparser',
            'fragment_policy': 'http_resource_url_without_fragment'},
        'source_assertions_validated': False, 'network_used': False, 'scripts_executed': False,
        'fallback_after_invocation': False, 'fidelity': 'selected_algorithm_projection_of_saved_source_bytes'}
