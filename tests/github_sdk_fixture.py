"""Controlled HTTP responses for the real PyGithub SDK; never a live account."""
from __future__ import annotations

import io
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

TEST_CREDENTIAL = 'test-credential-never-valid-at-github-0123456789'


class CountingBytes(io.BytesIO):
    def __init__(self, content):
        super().__init__(content)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)

    def release_conn(self):
        # Requests calls the urllib3 release hook after fully consuming a body.
        # This in-memory fixture models that ownership release by closing it.
        self.close()


class GitHubFixture:
    def __init__(self, configuration=None):
        self.configuration = configuration or {}
        self.repository = self.configuration.get('repository', 'acme/fixture')
        self.base = 'https://api.github.com/repos/' + self.repository
        self.seen = []
        self.responses = []
        self.streams = []

    def get(self, session, url, **kwargs):
        assert kwargs['stream'] is True and kwargs['verify'] is True and kwargs['allow_redirects'] is False
        assert session.trust_env is False and kwargs['data'] is None and 0 < kwargs['timeout'] <= 5
        assert kwargs['headers']['Authorization'].endswith(TEST_CREDENTIAL)
        self.seen.append(url)
        if self.configuration.get('marker'):
            Path(self.configuration['marker']).write_text(str(len(self.seen)))
        if self.configuration.get('delay_seconds'):
            time.sleep(self.configuration['delay_seconds'])
        parsed = urlsplit(url)
        assert parsed.scheme == 'https' and parsed.netloc == 'api.github.com'
        page = int(parse_qs(parsed.query).get('page', ['1'])[0])
        page_size = self.configuration.get('page_size', 2)
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        if parsed.path.endswith('/branches'):
            total = self.configuration.get('branches', 5)
            indices = range((page - 1) * page_size, min(page * page_size, total))
            prefix = self.configuration.get('branch_prefix', 'branch')
            body = [{'name': TEST_CREDENTIAL if self.configuration.get('reflect_credential') else f'{prefix}-{i}',
                'protected': i % 2 == 0, 'commit': {'sha': format(i + 1, '040x'), 'url': self.base + '/commits/' + format(i + 1, '040x')}}
                for i in indices]
            if page * page_size < total:
                link = self.configuration.get('next_link', self.base + f'/branches?page={page + 1}&per_page=100')
                headers['Link'] = '<' + link + '>; rel="next"'
        elif parsed.path.endswith('/actions/workflows'):
            total = self.configuration.get('workflows', 3)
            indices = range((page - 1) * page_size, min(page * page_size, total))
            body = {'total_count': total, 'workflows': [{'id': i + 1, 'name': f'Workflow {i}',
                'path': f'.github/workflows/{i}.yml', 'state': 'active', 'url': self.base + f'/actions/workflows/{i + 1}'} for i in indices]}
            if page * page_size < total:
                headers['Link'] = f'<{self.base}/actions/workflows?page={page + 1}&per_page=100>; rel="next"'
        else:
            assert parsed.path == '/repos/' + self.repository
            body = {'full_name': self.configuration.get('reported_repository', self.repository),
                'name': self.repository.split('/')[1], 'url': self.base,
                'default_branch': 'main', 'private': True, 'archived': False}
            if self.configuration.get('padding'):
                body['description'] = 'x' * self.configuration['padding']
        raw = json.dumps(body).encode()
        if self.configuration.get('content_length'):
            headers['Content-Length'] = str(len(raw))
        if self.configuration.get('large_header'):
            headers['X-Fixture'] = 'x' * 65_537
        response = requests.Response()
        response.status_code = self.configuration.get('status', 200)
        response.headers.update(headers)
        if 300 <= response.status_code < 400:
            response.headers['Location'] = self.configuration.get('redirect', 'https://example.invalid/private')
        stream = CountingBytes(raw)
        response.raw = stream
        response.encoding = 'utf-8'
        self.streams.append(stream)
        self.responses.append(response)
        return response

    def install(self, monkeypatch):
        monkeypatch.setattr(requests.Session, 'get', lambda session, url, **kwargs: self.get(session, url, **kwargs))
