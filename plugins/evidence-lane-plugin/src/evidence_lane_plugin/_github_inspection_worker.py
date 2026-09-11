"""Private PyGithub transport and inspection; no project database or writer access."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit


def inspect(request, token: str, expires_at: str) -> dict:
    import importlib.metadata

    import requests
    from github import Auth, Github
    from github.Requester import (
        HTTPRequestsConnectionClass,
        HTTPSRequestsConnectionClass,
        Requester,
        RequestsResponse,
    )

    from evidence_lane_plugin.errors import LaneError
    from evidence_lane_plugin.github_toolchain import (
        GITHUB_BACKEND_VERSION,
        GitHubPageCursor,
        _github_api_path,
        _validate_github_inspection,
    )
    from evidence_lane_plugin.hashing import canonical_json_bytes

    if importlib.metadata.version('PyGithub') != GITHUB_BACKEND_VERSION:
        raise LaneError('GITHUB_SDK_VERSION_MISMATCH', 'Use the exact registered PyGithub backend version.')
    deadline = time.monotonic() + request.timeout_seconds
    expiry = datetime.fromisoformat(expires_at) if expires_at != 'NO_EXPIRY' else None
    if expiry is not None and expiry.tzinfo is None:
        raise LaneError('GITHUB_GRANT_INVALID', 'The GitHub grant expiry must include a timezone.')
    observations: list[dict] = []
    total_bytes = 0
    expected_endpoint = '/repos/' + request.repository
    latest_page: dict = {}

    def check():
        if time.monotonic() >= deadline:
            raise LaneError('GITHUB_EXECUTION_BUDGET', 'The GitHub operation exhausted its deadline.')
        if expiry is not None and datetime.now(UTC) >= expiry:
            raise LaneError('GITHUB_GRANT_EXPIRED', 'The GitHub grant expired during inspection.')

    class DeniedHTTP(HTTPRequestsConnectionClass):
        def __init__(self, *args, **kwargs):
            raise LaneError('GITHUB_ORIGIN_DENIED', 'GitHub inspection requires its exact TLS API origin.')

    class BoundedHTTPS(HTTPSRequestsConnectionClass):
        def __init__(self, host, port=None, *args, **kwargs):
            if host != 'api.github.com' or port not in {None, 443} or kwargs.get('verify', True) is not True:
                raise LaneError('GITHUB_ORIGIN_DENIED', 'The GitHub request left its allowed API origin.')
            super().__init__(host, port, *args, **kwargs)
            self.session.trust_env = False

        def getresponse(self):
            nonlocal total_bytes
            check()
            if (self.verb != 'GET' or self.input is not None or not _github_api_path(self.url, request.repository)
                    or urlsplit(self.url).path.casefold() != expected_endpoint.casefold()):
                raise LaneError('GITHUB_RESOURCE_SCOPE_DENIED', 'The GitHub request left its exact repository endpoint scope.')
            if len(observations) >= request.max_http_requests:
                raise LaneError('GITHUB_HTTP_REQUEST_BUDGET', 'The GitHub operation exhausted its request budget.')
            url = 'https://api.github.com' + self.url
            try:
                response = self.session.get(url, headers=self.headers, data=None,
                    timeout=min(5.0, deadline - time.monotonic()), verify=True, allow_redirects=False, stream=True)
            except requests.RequestException:
                raise LaneError('GITHUB_NETWORK_FAILED', 'The GitHub request could not complete within its transport contract.') from None
            try:
                if response.status_code != 200:
                    code = ('GITHUB_REDIRECT_DENIED' if 300 <= response.status_code < 400 else
                        'GITHUB_RATE_LIMITED' if response.status_code in {403, 429} else
                        'GITHUB_AUTHENTICATION_FAILED' if response.status_code == 401 else 'GITHUB_HTTP_STATUS')
                    raise LaneError(code, 'The GitHub API did not return a complete permitted response.')
                if sum(len(str(key)) + len(str(value)) for key, value in response.headers.items()) > 65_536:
                    raise LaneError('GITHUB_HTTP_HEADER_BUDGET', 'The GitHub headers exceeded their byte budget.')
                length = response.headers.get('Content-Length')
                if length is not None and (not length.isascii() or not length.isdigit() or int(length) > request.max_response_bytes):
                    raise LaneError('GITHUB_HTTP_RESPONSE_BUDGET', 'The GitHub response exceeds its byte budget.')
                body = bytearray()
                for chunk in response.iter_content(chunk_size=65_536):
                    check()
                    if len(body) + len(chunk) > request.max_response_bytes or total_bytes + len(chunk) > request.max_total_response_bytes:
                        raise LaneError('GITHUB_HTTP_RESPONSE_BUDGET', 'The GitHub operation exhausted its response byte budget.')
                    body.extend(chunk)
                    total_bytes += len(chunk)
                # PyGithub's normal RequestsResponse sees only bounded content.
                response._content = bytes(body)
                response._content_consumed = True
                digest = hashlib.sha256(body).hexdigest()
                page = int(parse_qs(urlsplit(self.url).query).get('page', ['1'])[0])
                next_page = None
                link = response.links.get('next')
                if link is not None:
                    next_url = urlsplit(urljoin(url, link['url']))
                    next_path = next_url.path + ('?' + next_url.query if next_url.query else '')
                    query = parse_qs(next_url.query)
                    if (next_url.scheme != 'https' or next_url.hostname != 'api.github.com'
                            or next_url.port not in {None, 443} or next_url.username or next_url.password or next_url.fragment
                            or not _github_api_path(next_path, request.repository)
                            or next_url.path.casefold() != expected_endpoint.casefold()
                            or query.get('per_page') != ['100'] or query.get('page') != [str(page + 1)]):
                        raise LaneError('GITHUB_RESOURCE_SCOPE_DENIED', 'The GitHub continuation left its exact collection or pagination contract.')
                    next_page = page + 1
                latest_page.clear()
                latest_page.update(page=page, next_page=next_page, page_sha256=digest, endpoint=expected_endpoint)
                observations.append({'path': self.url, 'status': response.status_code,
                    'response_bytes': len(body), 'response_sha256': digest})
                return RequestsResponse(response)
            except requests.RequestException:
                raise LaneError('GITHUB_NETWORK_FAILED', 'The GitHub response could not be read within its transport contract.') from None
            finally:
                response.close()

    def collect(collection, limit, cursor, convert):
        page, offset, expected_hash = cursor.page, cursor.offset, cursor.page_sha256
        result = []
        while True:
            check()
            rows = collection.get_page(page - 1)
            if len(rows) > 100 or latest_page.get('page') != page or latest_page.get('endpoint') != expected_endpoint:
                raise LaneError('GITHUB_PAGE_RESULT_INVALID', 'The SDK page violated its exact collection contract.')
            if expected_hash is not None and latest_page['page_sha256'] != expected_hash:
                raise LaneError('GITHUB_CURSOR_PAGE_CHANGED', 'The page changed since its continuation position was observed.')
            if offset > len(rows):
                raise LaneError('GITHUB_CURSOR_OUT_OF_RANGE', 'The continuation position is outside its observed page.')
            for index in range(offset, len(rows)):
                result.append(convert(rows[index]))
                if len(result) == limit:
                    continuation = (GitHubPageCursor(page=page, offset=index + 1, page_sha256=latest_page['page_sha256'])
                        if index + 1 < len(rows) else GitHubPageCursor(page=latest_page['next_page'])
                        if latest_page['next_page'] is not None else None)
                    return result, continuation.model_dump(mode='json') if continuation is not None else None
            if latest_page['next_page'] is None:
                return result, None
            page, offset, expected_hash = latest_page['next_page'], 0, None

    Requester.injectConnectionClasses(DeniedHTTP, BoundedHTTPS)
    client = Github(auth=Auth.Token(token), timeout=5, retry=None, per_page=100,
        seconds_between_requests=0, seconds_between_writes=0, lazy=False)
    try:
        repository = client.get_repo(request.repository)
        raw = repository.raw_data
        if not isinstance(raw.get('full_name'), str) or raw['full_name'].casefold() != request.repository.casefold():
            raise LaneError('GITHUB_REPOSITORY_MISMATCH', 'The GitHub result belongs to another repository.')
        expected_endpoint = '/repos/' + request.repository + '/branches'
        def branch_data(branch):
            data = branch.raw_data
            return {'name': data['name'], 'commit': data['commit']['sha'], 'protected': data['protected']}
        branches, next_branch = collect(repository.get_branches(), request.max_branches, request.branch_cursor, branch_data)
        workflows, next_workflow = [], None
        if request.include_workflows:
            expected_endpoint = '/repos/' + request.repository + '/actions/workflows'
            def workflow_data(workflow):
                # raw_data requests full completion even when list metadata is
                # already present. Read the original API's populated fields.
                return {'id': workflow.id, 'name': workflow.name, 'path': workflow.path, 'state': workflow.state}
            workflows, next_workflow = collect(repository.get_workflows(), request.max_workflows, request.workflow_cursor, workflow_data)
        result = {'schema': 'evidence-lane.github-inspection.v4', 'engine': 'PyGithub', 'engine_version': GITHUB_BACKEND_VERSION,
            'repository': raw['full_name'], 'default_branch': raw['default_branch'], 'private': raw['private'], 'archived': raw['archived'],
            'branches': branches, 'workflows': workflows,
            'branch_listing_complete': next_branch is None,
            'workflow_listing_complete': request.include_workflows and next_workflow is None,
            'next_branch_cursor': next_branch, 'next_workflow_cursor': next_workflow,
            'remote_observation_atomic': False,
            'http_observations': observations, 'http_requests': len(observations), 'http_response_bytes': total_bytes,
            'observed_at': datetime.now(UTC).isoformat()}
        check()
        _validate_github_inspection(result, request)
        encoded = canonical_json_bytes(result)
        if len(encoded) > request.max_metadata_bytes or token.encode() in encoded:
            raise LaneError('GITHUB_RESULT_BUDGET_OR_SECRET', 'The GitHub result violated its output boundary.')
        return result
    finally:
        client.close()
        Requester.resetConnectionClasses()


def main():
    with (Path.cwd() / 'request.json').open('rb') as stream:
        raw = stream.read(65_537)
    if len(sys.argv) != 2 or len(raw) > 65_536 or hashlib.sha256(raw).hexdigest() != sys.argv[1]:
        raise ValueError('GITHUB_REQUEST_BINDING_INVALID')
    body = json.loads(raw)
    if not isinstance(body, dict) or set(body) != {'schema', 'arguments', 'expires_at', 'programs'}:
        raise ValueError('GITHUB_REQUEST_INVALID')
    if body['schema'] != 'evidence-lane.github-inspection-request.v4':
        raise ValueError('GITHUB_REQUEST_INVALID')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from evidence_lane_plugin.github_toolchain import GitHubInspectionRequest, _github_programs
    from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes

    programs = _github_programs()
    if body['programs'] != programs:
        raise ValueError('GITHUB_PROGRAM_BINDING_INVALID')
    token = os.environ.pop('_EVI_GITHUB_TOKEN', '')
    if not 20 <= len(token) <= 4096 or any(ord(character) < 33 or ord(character) > 126 for character in token):
        raise ValueError('GITHUB_CREDENTIAL_UNAVAILABLE')
    request = GitHubInspectionRequest.model_validate(body['arguments'])
    result = inspect(request, token, body['expires_at'])
    print(json.dumps({'status': 'ok', 'request_sha256': sys.argv[1],
        'program_sha256': sha256_bytes(canonical_json_bytes(programs)).lower(), 'inspection': result}, separators=(',', ':')))


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    try:
        main()
    except Exception as error:  # noqa: BLE001 - no vendor text or credentials may escape the private worker
        code = getattr(error, 'code', None)
        if code not in {'GITHUB_SDK_VERSION_MISMATCH', 'GITHUB_GRANT_INVALID', 'GITHUB_EXECUTION_BUDGET',
                'GITHUB_GRANT_EXPIRED', 'GITHUB_ORIGIN_DENIED', 'GITHUB_RESOURCE_SCOPE_DENIED',
                'GITHUB_HTTP_REQUEST_BUDGET', 'GITHUB_NETWORK_FAILED', 'GITHUB_REDIRECT_DENIED',
                'GITHUB_RATE_LIMITED', 'GITHUB_AUTHENTICATION_FAILED', 'GITHUB_HTTP_STATUS',
                'GITHUB_HTTP_HEADER_BUDGET', 'GITHUB_HTTP_RESPONSE_BUDGET', 'GITHUB_REPOSITORY_MISMATCH',
                'GITHUB_RESULT_INVALID', 'GITHUB_RESULT_BUDGET_OR_SECRET', 'GITHUB_PAGE_RESULT_INVALID',
                'GITHUB_CURSOR_PAGE_CHANGED', 'GITHUB_CURSOR_OUT_OF_RANGE'}:
            code = 'GITHUB_WORKER_FAILED'
        print(json.dumps({'status': 'error', 'error_code': code}), flush=True)
        raise SystemExit(1) from None
