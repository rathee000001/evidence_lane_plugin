"""Byte-bounded transports; the owning engine authorizes network use."""
from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import ipaddress
import socket
import threading
import time
import zlib
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

from .research_web_content import MAX_INPUT, safe_url, sha

HEADERS = {'User-Agent': 'EvidenceLane/4.0 source-intake', 'Accept-Encoding': 'identity',
           'Accept': 'text/html,application/xhtml+xml,text/plain,application/json,*/*;q=0.1'}
RETAINED_HEADERS = ('content-type', 'content-length', 'content-encoding', 'etag', 'last-modified', 'date', 'location')
_RESOLUTION_LOCK = threading.RLock()


def before(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('RESEARCH_WEB_DEADLINE')
    return min(5.0, remaining)


def selected_headers(headers):
    result = {key: str(headers[key]) for key in RETAINED_HEADERS if key in headers}
    if any(len(value.encode()) > 8192 for value in result.values()):
        raise ValueError('RESEARCH_WEB_HEADER_BUDGET')
    return result


def _host_key(value):
    if isinstance(value, bytes):
        value = value.decode('ascii', errors='strict')
    return str(value).rstrip('.').encode('idna').decode('ascii').casefold()


def _public_address(value):
    try:
        address = ipaddress.ip_address(str(value).split('%', 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


@contextmanager
def pinned_public_destination(url, *, _allow_non_public_for_tests=False):
    """Resolve once, reject private destinations, and pin the socket lookup.

    HTTPX and Requests both use ``socket.getaddrinfo`` for their direct socket.
    ``trust_env=False`` disables proxy indirection. The worker executes one
    operation at a time; the process-local lock prevents another thread from
    observing the temporary resolver while this connection is open.
    """
    parsed = urlsplit(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if host is None:
        raise ValueError('RESEARCH_WEB_URL_INVALID')
    expected_host = _host_key(host)
    with _RESOLUTION_LOCK:
        resolver = socket.getaddrinfo
        try:
            records = resolver(host, port, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            raise ValueError('RESEARCH_WEB_DESTINATION_UNRESOLVED') from None
        if not records:
            raise ValueError('RESEARCH_WEB_DESTINATION_UNRESOLVED')
        addresses = sorted({str(record[4][0]).split('%', 1)[0] for record in records})
        if not _allow_non_public_for_tests and any(not _public_address(value) for value in addresses):
            raise ValueError('RESEARCH_WEB_DESTINATION_DENIED')

        def pinned(node, service, family=0, type=0, proto=0, flags=0):
            try:
                node_key = _host_key(node)
                selected_port = int(service)
            except (TypeError, ValueError, UnicodeError):
                raise socket.gaierror(socket.EAI_NONAME, 'destination changed') from None
            if node_key != expected_host or selected_port != port:
                raise socket.gaierror(socket.EAI_NONAME, 'destination changed')
            selected = [record for record in records
                if family in {0, socket.AF_UNSPEC, record[0]}
                and type in {0, record[1]} and proto in {0, record[2]}]
            if not selected:
                raise socket.gaierror(socket.EAI_NONAME, 'destination family changed')
            return list(selected)

        socket.getaddrinfo = pinned
        try:
            yield {
                'address_count': len(addresses),
                'addresses_sha256': hashlib.sha256(
                    '\n'.join(addresses).encode('ascii')
                ).hexdigest(),
                'dns_rebinding_prevented': True,
            }
        finally:
            socket.getaddrinfo = resolver


@contextmanager
def response_stream(url, transport, deadline):
    if transport == 'httpx':
        import httpx
        with (httpx.Client(trust_env=False, follow_redirects=False, timeout=before(deadline)) as client,
              client.stream('GET', url, headers=HEADERS) as response):
            yield response.status_code, selected_headers(response.headers), response.iter_raw()
    elif transport == 'requests':
        import requests
        with requests.Session() as client:
            client.trust_env = False
            # One new session per hop prevents response cookies becoming request credentials.
            with client.get(url, headers=HEADERS, allow_redirects=False, stream=True,
                            timeout=before(deadline), verify=True) as response:
                def chunks():
                    while True:
                        before(deadline)
                        chunk = response.raw.read1(65_536, decode_content=False)
                        if not chunk:
                            break
                        yield chunk
                yield response.status_code, selected_headers(response.headers), chunks()
    else:
        raise ValueError('RESEARCH_WEB_TRANSPORT_INVALID')


def capture(url, transport, *, max_bytes=MAX_INPUT, timeout_seconds=20, max_redirects=5,
        _allow_non_public_for_tests=False):
    """Capture one selected GET chain, without retries, scripts or child-resource fetches."""
    if (not 1024 <= max_bytes <= MAX_INPUT or not 1 <= timeout_seconds <= 60
            or not 0 <= max_redirects <= 8 or transport not in {'httpx', 'requests'}):
        raise ValueError('RESEARCH_WEB_CAPTURE_BOUND')
    requested = safe_url(url)
    current, seen, redirects, destinations = requested, set(), [], []
    deadline = time.monotonic() + timeout_seconds
    observed_at = datetime.now(UTC).isoformat()
    while True:
        before(deadline)
        if current in seen:
            raise ValueError('RESEARCH_WEB_REDIRECT_LOOP')
        seen.add(current)
        with pinned_public_destination(
            current, _allow_non_public_for_tests=_allow_non_public_for_tests
        ) as destination_receipt:
            import validators
            if validators.url(current, simple_host=True) is not True:
                raise ValueError('RESEARCH_WEB_URL_INVALID')
            destinations.append({'url': current, **destination_receipt})
            with response_stream(current, transport, deadline) as (status, headers, stream):
                before(deadline)
                if status in {301, 302, 303, 307, 308}:
                    if len(redirects) >= max_redirects or not headers.get('location'):
                        raise ValueError('RESEARCH_WEB_REDIRECT_BOUND')
                    destination = safe_url(urljoin(current, headers['location']))
                    redirects.append({'url': current, 'status': status, 'location': destination})
                    current = destination
                    continue
                if not 200 <= status < 300:
                    raise ValueError('RESEARCH_WEB_HTTP_STATUS')
                length = headers.get('content-length')
                if length is not None and (not length.isdecimal() or int(length) > max_bytes):
                    raise ValueError('RESEARCH_WEB_RESPONSE_BUDGET')
                encoding = headers.get('content-encoding', 'identity').lower().strip()
                if encoding not in {'identity', 'gzip', 'deflate'}:
                    raise ValueError('RESEARCH_WEB_CONTENT_ENCODING_UNSUPPORTED')
                decompressor = zlib.decompressobj(31 if encoding == 'gzip' else 15) if encoding != 'identity' else None
                wire, body = bytearray(), bytearray()
                for chunk in stream:
                    before(deadline)
                    if len(wire) + len(chunk) > max_bytes:
                        raise ValueError('RESEARCH_WEB_RESPONSE_BUDGET')
                    wire.extend(chunk)
                    decoded = decompressor.decompress(chunk, max_bytes - len(body) + 1) if decompressor else chunk
                    body.extend(decoded)
                    if len(body) > max_bytes or (decompressor and decompressor.unconsumed_tail):
                        raise ValueError('RESEARCH_WEB_DECODED_BUDGET')
                before(deadline)
                if length is not None and len(wire) != int(length):
                    raise ValueError('RESEARCH_WEB_LENGTH_MISMATCH')
                if decompressor and (not decompressor.eof or decompressor.unused_data):
                    raise ValueError('RESEARCH_WEB_COMPRESSED_BODY_INVALID')
                break
    import tldextract
    # The shipped public-suffix snapshot is used without cache writes or remote updates.
    domains = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
    versions = {name: importlib.metadata.version(name) for name in
                (transport, 'validators', 'tldextract', *(('urllib3',) if transport == 'requests' else ())) }
    return {'schema': 'evidence-lane.research-web-capture.v4', 'requested_url': requested, 'final_url': current,
        'captured_at': observed_at, 'completed_at': datetime.now(UTC).isoformat(), 'transport': transport,
        'versions': versions, 'redirects': redirects, 'destination_checks': destinations,
        'destination_policy': 'public_addresses_only_with_single_resolution_per_hop',
        'status_code': status, 'headers': headers,
        'registrable_domain': domains(current).top_domain_under_public_suffix or urlsplit(current).hostname,
        'wire_bytes': len(wire), 'wire_sha256': sha(wire), 'wire_base64': base64.b64encode(wire).decode('ascii'),
        'body_bytes': len(body), 'body_sha256': sha(body), 'body_base64': base64.b64encode(body).decode('ascii'),
        'deadline_scope': 'checked_before_and_after_each_transport_read',
        'ambient_proxy_or_credentials_used': False, 'scripts_executed': False, 'automatic_child_fetch': False,
        'automatic_retry': False, 'fallback_after_invocation': False,
        'remote_currentness': 'one_observed_response_chain_not_rechecked'}
