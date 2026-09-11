"""Actual PyGithub pagination and bounded transport against controlled responses."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from evidence_lane_plugin._github_inspection_worker import inspect
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.github_toolchain import GitHubInspectionRequest, _github_api_path

from .github_sdk_fixture import TEST_CREDENTIAL, GitHubFixture


def run(monkeypatch, configuration=None, **arguments):
    fixture = GitHubFixture(configuration)
    fixture.install(monkeypatch)
    result = inspect(GitHubInspectionRequest(repository=fixture.repository, **arguments), TEST_CREDENTIAL, 'NO_EXPIRY')
    return result, fixture


def test_real_sdk_stops_pagination_at_each_selected_record_bound(monkeypatch):
    result, fixture = run(monkeypatch, max_branches=3, max_workflows=2)
    assert len(result['branches']) == 3 and len(result['workflows']) == 2
    assert result['http_requests'] == len(fixture.seen) == 4
    assert not result['branch_listing_complete'] and not result['workflow_listing_complete']
    assert all(stream.closed and all(size > 0 for size in stream.read_sizes) for stream in fixture.streams)
    assert TEST_CREDENTIAL not in json.dumps(result)


def test_real_sdk_exhaustion_and_disabled_workflows_are_attributed(monkeypatch):
    result, fixture = run(monkeypatch, {'branches': 1}, include_workflows=False)
    assert result['branch_listing_complete'] and result['workflows'] == []
    assert not result['workflow_listing_complete'] and len(fixture.seen) == 2
    assert not any('workflows' in url for url in fixture.seen)


def test_continuation_resumes_partial_and_complete_pages_without_skipping_records(monkeypatch):
    first, _ = run(monkeypatch, max_branches=3, max_workflows=2)
    assert first['next_branch_cursor']['page'] == 2 and first['next_branch_cursor']['offset'] == 1
    assert first['next_branch_cursor']['page_sha256']
    assert first['next_workflow_cursor'] == {'page': 2, 'offset': 0, 'page_sha256': None}
    second, fixture = run(monkeypatch, branch_cursor=first['next_branch_cursor'], workflow_cursor=first['next_workflow_cursor'])
    assert [row['name'] for row in first['branches'] + second['branches']] == [f'branch-{i}' for i in range(5)]
    assert [row['id'] for row in first['workflows'] + second['workflows']] == [1, 2, 3]
    assert second['branch_listing_complete'] and second['workflow_listing_complete']
    assert second['next_branch_cursor'] is None and second['next_workflow_cursor'] is None
    assert not second['remote_observation_atomic']
    assert len(fixture.seen) == 4 and '/branches?page=2' in fixture.seen[1]


def test_workflow_catalog_beyond_one_call_limit_remains_accessible(monkeypatch):
    configuration = {'page_size': 100, 'branches': 1, 'workflows': 501}
    first, _ = run(monkeypatch, configuration, max_workflows=500)
    assert len(first['workflows']) == 500 and first['next_workflow_cursor']['page'] == 6
    second, fixture = run(monkeypatch, configuration, workflow_cursor=first['next_workflow_cursor'])
    assert [row['id'] for row in first['workflows'] + second['workflows']] == list(range(1, 502))
    assert second['workflow_listing_complete'] and len(fixture.seen) == 3


def test_later_page_is_retrieved_without_consuming_earlier_pages(monkeypatch):
    result, fixture = run(monkeypatch, {'branches': 10_000, 'page_size': 100},
        branch_cursor={'page': 80}, max_branches=1, max_http_requests=2, include_workflows=False)
    assert result['branches'][0]['name'] == 'branch-7900'
    assert len(fixture.seen) == 2 and '/branches?page=80' in fixture.seen[1]
    assert result['next_branch_cursor']['page'] == 80 and result['next_branch_cursor']['offset'] == 1


def test_changed_partial_page_cannot_be_resumed_under_the_old_hash(monkeypatch):
    first, _ = run(monkeypatch, max_branches=3, include_workflows=False)
    fixture = GitHubFixture({'branch_prefix': 'changed'})
    fixture.install(monkeypatch)
    with pytest.raises(LaneError) as failure:
        inspect(GitHubInspectionRequest(repository=fixture.repository, branch_cursor=first['next_branch_cursor'],
            include_workflows=False), TEST_CREDENTIAL, 'NO_EXPIRY')
    assert failure.value.code == 'GITHUB_CURSOR_PAGE_CHANGED' and len(fixture.seen) == 2


def test_cursor_cannot_skip_past_the_end_of_its_bound_page(monkeypatch):
    first, _ = run(monkeypatch, max_branches=3, include_workflows=False)
    fixture = GitHubFixture()
    fixture.install(monkeypatch)
    cursor = dict(first['next_branch_cursor'], offset=99)
    with pytest.raises(LaneError) as failure:
        inspect(GitHubInspectionRequest(repository=fixture.repository, branch_cursor=cursor,
            include_workflows=False), TEST_CREDENTIAL, 'NO_EXPIRY')
    assert failure.value.code == 'GITHUB_CURSOR_OUT_OF_RANGE'


def test_exact_end_at_record_limit_uses_observed_page_end(monkeypatch):
    result, fixture = run(monkeypatch, {'branches': 2}, max_branches=2, include_workflows=False)
    assert len(result['branches']) == 2 and result['branch_listing_complete'] and result['next_branch_cursor'] is None
    assert len(fixture.seen) == 2


@pytest.mark.parametrize(('status', 'code'), [(202, 'GITHUB_HTTP_STATUS'), (301, 'GITHUB_REDIRECT_DENIED'),
    (302, 'GITHUB_REDIRECT_DENIED'), (401, 'GITHUB_AUTHENTICATION_FAILED'), (403, 'GITHUB_RATE_LIMITED'),
    (429, 'GITHUB_RATE_LIMITED'), (500, 'GITHUB_HTTP_STATUS')])
def test_non_success_never_retries_redirects_or_sleeps(monkeypatch, status, code):
    fixture = GitHubFixture({'status': status})
    fixture.install(monkeypatch)
    def no_sleep(*args):
        raise AssertionError('The SDK attempted an implicit wait')
    monkeypatch.setattr('time.sleep', no_sleep)
    with pytest.raises(LaneError) as failure:
        inspect(GitHubInspectionRequest(repository=fixture.repository), TEST_CREDENTIAL, 'NO_EXPIRY')
    assert failure.value.code == code and len(fixture.seen) == 1
    assert fixture.streams[0].closed and not fixture.streams[0].read_sizes
    assert TEST_CREDENTIAL not in str(failure.value)


@pytest.mark.parametrize('link', [
    'https://api.github.com/repos/acme/other/branches?page=2',
    'https://api.github.com/repos/acme/fixture/actions/workflows?page=2',
    'https://api.github.com/repos/acme/fixture/branches?page=2&per_page=1000',
    'https://api.github.com/repos/acme/fixture/branches?page=2&page=3',
])
def test_pagination_cannot_escape_repository_collection_or_query_bounds(monkeypatch, link):
    fixture = GitHubFixture({'next_link': link})
    fixture.install(monkeypatch)
    with pytest.raises(LaneError) as failure:
        inspect(GitHubInspectionRequest(repository=fixture.repository, include_workflows=False), TEST_CREDENTIAL, 'NO_EXPIRY')
    assert failure.value.code == 'GITHUB_RESOURCE_SCOPE_DENIED'
    assert len(fixture.seen) == 2 and all(stream.closed for stream in fixture.streams)


@pytest.mark.parametrize('declared_length', [False, True])
def test_response_body_budget_applies_before_sdk_json_allocation(monkeypatch, declared_length):
    fixture = GitHubFixture({'padding': 5000, 'content_length': declared_length})
    fixture.install(monkeypatch)
    with pytest.raises(LaneError) as failure:
        inspect(GitHubInspectionRequest(repository=fixture.repository, max_response_bytes=1024), TEST_CREDENTIAL, 'NO_EXPIRY')
    assert failure.value.code == 'GITHUB_HTTP_RESPONSE_BUDGET' and fixture.streams[0].closed
    if declared_length:
        assert not fixture.streams[0].read_sizes


def test_total_response_budget_covers_several_individually_small_pages(monkeypatch):
    fixture = GitHubFixture({'branches': 5})
    fixture.install(monkeypatch)
    with pytest.raises(LaneError) as failure:
        inspect(GitHubInspectionRequest(repository=fixture.repository, max_response_bytes=1024,
            max_total_response_bytes=1024), TEST_CREDENTIAL, 'NO_EXPIRY')
    assert failure.value.code == 'GITHUB_HTTP_RESPONSE_BUDGET' and len(fixture.seen) > 1
    assert all(stream.closed for stream in fixture.streams)


@pytest.mark.parametrize(('configuration', 'arguments', 'code'), [
    ({}, {'max_http_requests': 1}, 'GITHUB_HTTP_REQUEST_BUDGET'),
    ({'large_header': True}, {}, 'GITHUB_HTTP_HEADER_BUDGET'),
    ({'reported_repository': 'acme/another'}, {}, 'GITHUB_REPOSITORY_MISMATCH'),
    ({'reflect_credential': True, 'branches': 1}, {}, 'GITHUB_RESULT_BUDGET_OR_SECRET'),
    ({}, {'max_metadata_bytes': 1024}, 'GITHUB_RESULT_BUDGET_OR_SECRET'),
])
def test_request_header_identity_metadata_and_secret_boundaries(monkeypatch, configuration, arguments, code):
    fixture = GitHubFixture(configuration)
    fixture.install(monkeypatch)
    with pytest.raises(LaneError) as failure:
        inspect(GitHubInspectionRequest(repository=fixture.repository, **arguments), TEST_CREDENTIAL, 'NO_EXPIRY')
    assert failure.value.code == code and all(stream.closed for stream in fixture.streams)


def test_expired_grant_stops_before_the_first_http_request(monkeypatch):
    fixture = GitHubFixture()
    fixture.install(monkeypatch)
    with pytest.raises(LaneError) as failure:
        inspect(GitHubInspectionRequest(repository=fixture.repository), TEST_CREDENTIAL,
            (datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    assert failure.value.code == 'GITHUB_GRANT_EXPIRED' and fixture.seen == []


@pytest.mark.parametrize('arguments', [
    {'repository': '../escape'}, {'repository': 'owner/..'}, {'repository': 'owner/a/b'},
    {'network_allowed': True}, {'host_profile': 'CODEX_DESKTOP'}, {'secret_reference': 'caller-secret'},
    {'max_branches': True}, {'max_branches': 501}, {'max_workflows': 0}, {'max_http_requests': 33},
    {'max_response_bytes': 1023}, {'max_total_response_bytes': 1024},
    {'timeout_seconds': float('nan')}, {'timeout_seconds': float('inf')}, {'timeout_seconds': False},
    {'branch_cursor': {'offset': 1}}, {'workflow_cursor': {'page': 0}},
    {'branch_cursor': {'page': True}}, {'workflow_cursor': {'page': 1_000_001}},
])
def test_request_rejects_unbound_authority_flags_and_invalid_budgets(arguments):
    with pytest.raises(ValueError):
        GitHubInspectionRequest(**({'repository': 'acme/fixture'} | arguments))


@pytest.mark.parametrize('path', ['https://api.github.com/repos/acme/fixture', '//other.invalid/path',
    '/repos/acme/fixture-more', '/repos/acme/fixture/../other', '/repos/acme/fixture?token=value',
    '/repos/acme/fixture?page=0', '/repos/acme/fixture?page=2#fragment'])
def test_parent_result_admission_requires_exact_api_paths(path):
    assert not _github_api_path(path, 'acme/fixture')
