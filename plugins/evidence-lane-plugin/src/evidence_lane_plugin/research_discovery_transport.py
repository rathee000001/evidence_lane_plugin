"""Bounded transport adapter for the pinned DDGS provider interface."""
from __future__ import annotations

import base64
import time
import zlib
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import urlencode, urljoin, urlsplit

from .research_web_content import safe_url, sha
from .research_web_fetch import HEADERS, before, selected_headers


class ProviderTransport:
    def __init__(self, *, deadline, budget, backend, roots):
        self.deadline, self.budget, self.backend, self.roots = deadline, budget, backend, roots
        self.client = self
        self.headers, self.cookies = {}, {}

    def headers_update(self, headers):
        if set(headers) - {'User-Agent', 'Referer'} or any(len(str(value).encode()) > 1024 for value in headers.values()):
            raise ValueError('RESEARCH_DISCOVERY_PROVIDER_HEADERS')
        self.headers.update(headers)

    def set_cookies(self, domain, cookies):
        host = urlsplit(domain).hostname if '://' in domain else domain
        if not self.allowed(host) or len(cookies) > 16:
            raise ValueError('RESEARCH_DISCOVERY_PROVIDER_COOKIES')
        if any(not str(key) or any(char in str(key) + str(value) for char in '\r\n;=')
            or len(str(key) + str(value)) > 1024 for key, value in cookies.items()):
            raise ValueError('RESEARCH_DISCOVERY_PROVIDER_COOKIES')
        self.cookies[host] = {str(key): str(value) for key, value in cookies.items()}

    def allowed(self, host):
        return bool(host) and any(host == root or host.endswith('.' + root) for root in self.roots)

    def request(self, method, url, *, params=None, data=None):
        import httpx
        method = method.upper()
        if method not in {'GET', 'POST'} or (params is not None and data is not None):
            raise ValueError('RESEARCH_DISCOVERY_PROVIDER_METHOD')
        if params:
            url += ('&' if '?' in url else '?') + urlencode(params)
        current = requested = safe_url(url)
        content = urlencode(data).encode() if data is not None else b''
        if len(content) > 8192:
            raise ValueError('RESEARCH_DISCOVERY_PAYLOAD_BUDGET')
        payload_digest, requested_method = sha(content), method
        observed_at, redirects, seen = datetime.now(UTC).isoformat(), [], set()
        while True:
            before(self.deadline)
            if current in seen or len(redirects) > 5 or not self.allowed(urlsplit(current).hostname):
                raise ValueError('RESEARCH_DISCOVERY_PROVIDER_REDIRECT')
            if self.budget['requests'] >= self.budget['max_requests']:
                raise ValueError('RESEARCH_DISCOVERY_REQUEST_BUDGET')
            seen.add(current)
            self.budget['requests'] += 1
            headers = {**HEADERS, **self.headers}
            if content:
                headers['Content-Type'] = 'application/x-www-form-urlencoded'
            cookies = {}
            host = urlsplit(current).hostname
            for domain, values in self.cookies.items():
                if host == domain or host.endswith('.' + domain):
                    cookies.update(values)
            if cookies:
                headers['Cookie'] = '; '.join(key + '=' + value for key, value in sorted(cookies.items()))
            # Provider preference cookies are explicit; response cookies, proxies
            # and ambient credentials are not reused by this fresh client.
            with (httpx.Client(trust_env=False, follow_redirects=False, timeout=before(self.deadline)) as client,
                  client.stream(method, current, headers=headers, content=content or None) as response):
                before(self.deadline)
                status, selected = response.status_code, selected_headers(response.headers)
                if status in {301, 302, 303, 307, 308}:
                    if not selected.get('location'):
                        raise ValueError('RESEARCH_DISCOVERY_PROVIDER_REDIRECT')
                    destination = safe_url(urljoin(current, selected['location']))
                    redirects.append({'url': current, 'status_code': status, 'destination': destination})
                    if status == 303 or (status in {301, 302} and method == 'POST'):
                        method, content = 'GET', b''
                    current = destination
                    continue
                if status != 200:
                    self.budget['events'].append({'backend': self.backend, 'url': current,
                        'status_code': status, 'response_body_stored': False, 'redirects': redirects})
                    raise ValueError('RESEARCH_DISCOVERY_HTTP_STATUS')
                limit = min(2_097_152, self.budget['max_bytes'] - self.budget['body_bytes'],
                    self.budget['max_bytes'] - self.budget['wire_bytes'])
                if limit < 1:
                    raise ValueError('RESEARCH_DISCOVERY_RESPONSE_BUDGET')
                length = selected.get('content-length')
                if length is not None and (not length.isdecimal() or int(length) > limit):
                    raise ValueError('RESEARCH_DISCOVERY_RESPONSE_BUDGET')
                encoding = selected.get('content-encoding', 'identity').lower().strip()
                if encoding not in {'identity', 'gzip', 'deflate'}:
                    raise ValueError('RESEARCH_DISCOVERY_CONTENT_ENCODING')
                decoder = zlib.decompressobj(31 if encoding == 'gzip' else 15) if encoding != 'identity' else None
                wire, body = bytearray(), bytearray()
                for chunk in response.iter_raw(chunk_size=65_536):
                    before(self.deadline)
                    self.budget['wire_bytes'] += len(chunk)
                    wire.extend(chunk)
                    if len(wire) > limit:
                        raise ValueError('RESEARCH_DISCOVERY_RESPONSE_BUDGET')
                    decoded = decoder.decompress(chunk, limit - len(body) + 1) if decoder else chunk
                    self.budget['body_bytes'] += len(decoded)
                    body.extend(decoded)
                    if len(body) > limit or (decoder and decoder.unconsumed_tail):
                        raise ValueError('RESEARCH_DISCOVERY_RESPONSE_BUDGET')
                before(self.deadline)
                if (length is not None and len(wire) != int(length)) or (decoder and (not decoder.eof or decoder.unused_data)):
                    raise ValueError('RESEARCH_DISCOVERY_RESPONSE_INTEGRITY')
            event = {'backend': self.backend, 'requested_url': requested, 'url': current,
                'method': requested_method, 'payload_sha256': payload_digest, 'observed_at': observed_at,
                'completed_at': datetime.now(UTC).isoformat(), 'status_code': status, 'headers': selected,
                'redirects': redirects, 'wire_bytes': len(wire), 'body_bytes': len(body),
                'wire_sha256': sha(wire), 'body_sha256': sha(body),
                'wire_base64': base64.b64encode(wire).decode(), 'body_base64': base64.b64encode(body).decode(),
                'response_body_stored': True, 'provider_preference_cookies_used': bool(cookies),
                'ambient_credentials_or_proxy_used': False}
            self.budget['events'].append(event)
            return SimpleNamespace(status_code=status, content=bytes(body), text=bytes(body).decode('utf-8', errors='strict'))


def budget(timeout, max_bytes, max_requests):
    return time.monotonic() + timeout, {'requests': 0, 'wire_bytes': 0, 'body_bytes': 0, 'events': [],
        'max_bytes': max_bytes, 'max_requests': max_requests}
