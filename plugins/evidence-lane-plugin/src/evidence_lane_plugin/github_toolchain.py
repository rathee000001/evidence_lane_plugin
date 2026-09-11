"""Bounded PyGithub inspection owned by a current project connector and Delta."""
from __future__ import annotations

import importlib.metadata
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, JsonValue, field_validator, model_validator

from .errors import LaneError
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .registry import Contract

GITHUB_BACKEND_ID = 'github.repository-reader'
GITHUB_BACKEND_VERSION = '2.10.0'
GITHUB_OPERATION = 'github_repository_inspect'
_REPOSITORY = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}')
_HEX = re.compile(r'[0-9a-f]{64}')


class GitHubPageCursor(Contract):
    page: int = Field(default=1, ge=1, le=1_000_000)
    offset: int = Field(default=0, ge=0, le=99)
    page_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def within_page_binding(self):
        if self.offset and self.page_sha256 is None:
            raise ValueError('A cursor within a page requires its observed response hash')
        return self


class GitHubInspectionRequest(Contract):
    repository: str = Field(min_length=3, max_length=140)
    plugin_id: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9-]{2,63}$')
    include_workflows: bool = True
    branch_cursor: GitHubPageCursor = Field(default_factory=GitHubPageCursor)
    workflow_cursor: GitHubPageCursor = Field(default_factory=GitHubPageCursor)
    max_branches: int = Field(default=100, ge=1, le=500)
    max_workflows: int = Field(default=100, ge=1, le=500)
    max_http_requests: int = Field(default=16, ge=1, le=32)
    max_response_bytes: int = Field(default=2_097_152, ge=1024, le=4_194_304)
    max_total_response_bytes: int = Field(default=8_388_608, ge=1024, le=33_554_432)
    max_metadata_bytes: int = Field(default=2_097_152, ge=1024, le=8_388_608)
    timeout_seconds: float = Field(default=20, gt=0, le=60, allow_inf_nan=False)

    @field_validator('repository')
    @classmethod
    def exact_repository(cls, value):
        if not _REPOSITORY.fullmatch(value) or value.split('/')[1] in {'.', '..'}:
            raise ValueError('Use one exact GitHub owner/repository identifier')
        return value

    @model_validator(mode='after')
    def total_response_budget(self):
        if self.max_total_response_bytes < self.max_response_bytes:
            raise ValueError('The total HTTP budget cannot be smaller than one response budget')
        return self


class GitHubInspectionResult(Contract):
    repository: str
    snapshot: dict[str, JsonValue]
    receipt_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


def _configured_token(registration: dict) -> tuple[str, str]:
    keys = registration.get('config_env_keys', [])
    if len(keys) != 1 or not isinstance(keys[0], str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{2,127}', keys[0]):
        raise LaneError('GITHUB_CREDENTIAL_CONFIGURATION_REQUIRED', 'Configure exactly one token environment reference for this GitHub grant.')
    token = os.environ.get(keys[0], '')
    if not 20 <= len(token) <= 4096 or any(ord(character) < 33 or ord(character) > 126 for character in token):
        raise LaneError('GITHUB_CREDENTIAL_UNAVAILABLE', 'The configured GitHub token reference is unavailable or invalid.')
    return keys[0], token


def github_readiness(context, registration):
    """Validate the configured SDK path without contacting GitHub."""
    if importlib.metadata.version('PyGithub') != GITHUB_BACKEND_VERSION:
        return {'ready': False, 'backend_version': GITHUB_BACKEND_VERSION}
    from github import Auth, Github

    _, token = _configured_token(registration)
    client = Github(auth=Auth.Token(token), timeout=5, retry=None, per_page=100,
        seconds_between_requests=0, seconds_between_writes=0, lazy=False)
    try:
        ready = callable(client.get_repo) and callable(client.requester.requestJsonAndCheck)
    finally:
        client.close()
    return {'ready': ready, 'backend_version': GITHUB_BACKEND_VERSION,
        'basis': 'sdk_constructor_and_exact_grant_credential_reference', 'remote_authentication_verified': False}


def _github_programs() -> dict[str, str]:
    return {name: sha256_file(Path(__file__).with_name(name)).lower() for name in (
        'github_toolchain.py', '_github_inspection_worker.py', 'bounded_io.py', '_bounded_process_child.py')}


def _github_api_path(value: str, repository: str) -> bool:
    try:
        if not isinstance(value, str) or len(value) > 1024:
            return False
        parsed = urlsplit(value)
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        base = '/repos/' + repository.casefold()
        return (not (parsed.scheme or parsed.netloc or parsed.fragment)
            and parsed.path.casefold() in {base, base + '/branches', base + '/actions/workflows'}
            and not set(query) - {'page', 'per_page'}
            and all(len(values) == 1 and values[0].isascii() and values[0].isdigit()
                and 1 <= int(values[0]) <= (100 if name == 'per_page' else 1_000_000) for name, values in query.items()))
    except (ValueError, TypeError):
        return False


def _validate_github_inspection(value: dict, request: GitHubInspectionRequest) -> None:
    def fail():
        raise LaneError('GITHUB_RESULT_INVALID', 'The GitHub inspection violated its exact result contract.')
    try:
        if set(value) != {'schema', 'engine', 'engine_version', 'repository', 'default_branch', 'private', 'archived',
                'branches', 'workflows', 'branch_listing_complete', 'workflow_listing_complete', 'http_observations',
                'http_requests', 'http_response_bytes', 'observed_at', 'next_branch_cursor', 'next_workflow_cursor',
                'remote_observation_atomic'}:
            fail()
        if (value['schema'] != 'evidence-lane.github-inspection.v4' or value['engine'] != 'PyGithub'
                or value['engine_version'] != GITHUB_BACKEND_VERSION or not isinstance(value['repository'], str)
                or value['repository'].casefold() != request.repository.casefold()
                or value['remote_observation_atomic'] is not False
                or any(type(value[key]) is not bool for key in ('private', 'archived', 'branch_listing_complete', 'workflow_listing_complete'))):
            fail()
        default = value['default_branch']
        if default is not None and (not isinstance(default, str) or not 1 <= len(default) <= 1024):
            fail()
        from datetime import datetime
        if datetime.fromisoformat(value['observed_at']).tzinfo is None:
            fail()
        branches, workflows, observations = value['branches'], value['workflows'], value['http_observations']
        if (not isinstance(branches, list) or len(branches) > request.max_branches
                or not isinstance(workflows, list) or len(workflows) > request.max_workflows
                or (not request.include_workflows and workflows)
                or not isinstance(observations, list) or not 1 <= len(observations) <= request.max_http_requests):
            fail()
        for row in branches:
            if (set(row) != {'name', 'commit', 'protected'} or not isinstance(row['name'], str)
                    or not 1 <= len(row['name']) <= 1024 or type(row['protected']) is not bool
                    or not isinstance(row['commit'], str) or re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', row['commit']) is None):
                fail()
        if len({row['name'] for row in branches}) != len(branches):
            fail()
        for row in workflows:
            if (set(row) != {'id', 'name', 'path', 'state'} or type(row['id']) is not int or not 0 < row['id'] <= 2**63 - 1
                    or any(not isinstance(row[key], str) or not 1 <= len(row[key]) <= 1024 for key in ('name', 'path', 'state'))):
                fail()
        if len({row['id'] for row in workflows}) != len(workflows):
            fail()
        for row in observations:
            if (set(row) != {'path', 'status', 'response_bytes', 'response_sha256'} or row['status'] != 200
                    or not _github_api_path(row['path'], request.repository)
                    or type(row['response_bytes']) is not int or not 0 <= row['response_bytes'] <= request.max_response_bytes
                    or not isinstance(row['response_sha256'], str) or not _HEX.fullmatch(row['response_sha256'])):
                fail()
        if (type(value['http_requests']) is not int or value['http_requests'] != len(observations)
                or type(value['http_response_bytes']) is not int
                or value['http_response_bytes'] != sum(row['response_bytes'] for row in observations)
                or value['http_response_bytes'] > request.max_total_response_bytes):
            fail()
        for key, start in (('next_branch_cursor', request.branch_cursor), ('next_workflow_cursor', request.workflow_cursor)):
            if value[key] is not None:
                cursor = GitHubPageCursor.model_validate(value[key])
                if (cursor.page, cursor.offset) <= (start.page, start.offset):
                    fail()
        if (value['branch_listing_complete'] != (value['next_branch_cursor'] is None)
                or value['workflow_listing_complete'] != (request.include_workflows and value['next_workflow_cursor'] is None)
                or (not request.include_workflows and value['next_workflow_cursor'] is not None)):
            fail()
    except (ValueError, KeyError, TypeError, AttributeError):
        fail()


def inspect_github_repository(request: GitHubInspectionRequest, *, engine=None, context=None) -> GitHubInspectionResult:
    """Resolve authority from the active engine Delta, never a caller network flag."""
    from .bounded_io import run_owned_bounded_process
    from .connector_governance import connector_service
    from .tool_routes import routes_for

    if engine is None or context is None or context.execution is None or context.execution.guard is None:
        raise LaneError('DELTA_REQUIRED', 'GitHub inspection requires the current project Delta and connector grant.')
    request = GitHubInspectionRequest.model_validate(request.model_dump())
    admission = context.tool_admission
    if not isinstance(admission, dict) or not isinstance(admission.get('extension'), dict):
        raise LaneError('GITHUB_ADMISSION_REQUIRED', 'Use the exact admitted GitHub connector operation.')
    spec = engine.registry.get(GITHUB_OPERATION)
    route = routes_for(spec)[0]
    proof = engine.registry.tool_router.extensions.authorize(spec, route, context, request, admission['extension'])
    service = connector_service(engine, context.execution.store)
    registration = next((row for row in service.active_catalog() if row['plugin_id'] == proof['plugin_id']
        and row['version'] == proof['registration_version'] and row['digest'] == proof['registration_digest']), None)
    if registration is None:
        raise LaneError('PLUGIN_VERSION_CONFLICT', 'The admitted GitHub registration changed.')
    key, token = _configured_token(registration)
    programs = _github_programs()
    program_digest = sha256_bytes(canonical_json_bytes(programs)).lower()
    body = {'schema': 'evidence-lane.github-inspection-request.v4', 'arguments': request.model_dump(mode='json'),
        'expires_at': proof['expires_at'], 'programs': programs}
    encoded = canonical_json_bytes(body)
    if len(encoded) > 65_536:
        raise LaneError('GITHUB_REQUEST_BUDGET', 'The GitHub request exceeded its private descriptor bound.')
    request_digest = sha256_bytes(encoded).lower()
    with tempfile.TemporaryDirectory(prefix='evidence-lane-github-') as directory:
        (Path(directory) / 'request.json').write_bytes(encoded)
        environment = {name: value for name, value in os.environ.items() if name.upper() in {
            'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'}}
        environment.update(TEMP=directory, TMP=directory, _EVI_GITHUB_TOKEN=token)
        result = run_owned_bounded_process([sys.executable, '-I', '-B',
            str(Path(__file__).with_name('_github_inspection_worker.py')), request_digest],
            cwd=directory, env=environment, timeout_seconds=request.timeout_seconds,
            max_stdout_bytes=request.max_metadata_bytes + 65_536, max_stderr_bytes=65_536,
            check=context.execution.check)
        context.execution.check()
        try:
            response = json.loads(result.stdout)
            if result.returncode != 0:
                code = response.get('error_code', 'GITHUB_WORKER_FAILED')
                if not isinstance(code, str) or not re.fullmatch(r'GITHUB_[A-Z_]{1,70}', code):
                    code = 'GITHUB_WORKER_FAILED'
                raise LaneError(code, 'The bounded GitHub inspection failed; no result was admitted.')
            if (response['status'] != 'ok' or response['request_sha256'] != request_digest
                    or response['program_sha256'] != program_digest or _github_programs() != programs):
                raise LaneError('GITHUB_WORKER_BINDING_CHANGED', 'The GitHub worker request or executable identity changed.')
            inspection = response['inspection']
            raw = canonical_json_bytes(inspection)
            if len(raw) > request.max_metadata_bytes or token.encode() in raw:
                raise LaneError('GITHUB_RESULT_BUDGET_OR_SECRET', 'The GitHub result violated its output boundary.')
            _validate_github_inspection(inspection, request)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise LaneError('GITHUB_RESULT_INVALID', 'The GitHub worker returned an invalid complete result.') from None
    snapshot = {**inspection, 'request_sha256': request_digest, 'program_sha256': program_digest,
        'grant': proof, 'credential_reference_sha256': sha256_bytes(key.encode()).lower(),
        'owned_worker_joined': True, 'credential_value_persisted': False, 'write_authorized': False,
        'request_limits': request.model_dump(mode='json')}
    return GitHubInspectionResult(repository=inspection['repository'], snapshot=snapshot,
        receipt_sha256=sha256_bytes(canonical_json_bytes(snapshot)).lower())


def verify_github_inspection(context, request, output):
    valid = (output.repository.casefold() == request.repository.casefold()
        and output.receipt_sha256 == sha256_bytes(canonical_json_bytes(output.snapshot)).lower()
        and output.snapshot.get('owned_worker_joined') is True and output.snapshot.get('write_authorized') is False)
    return [{'check_id': name, 'passed': valid, 'evidence': {'repository': output.repository,
        'receipt_sha256': output.receipt_sha256, 'remote_read_repeated_for_verification': False}} for name in context.requested_checks]


def register_github_actions(engine):
    from .extension_routes import ExtensionBinding
    from .registry import ActionSpec
    from .tool_routes import ToolRoute

    def handler(context, request):
        return inspect_github_repository(request, engine=engine, context=context)
    extension = ExtensionBinding(GITHUB_BACKEND_ID, GITHUB_BACKEND_VERSION, 'python', 'repository_metadata_read',
        'github_code', 'github_repository_snapshot',
        (('repository', 'text'), ('snapshot', 'json'), ('receipt_sha256', 'blob_hash')), github_readiness,
        resource_fields=('repository',), plugin_id_field='plugin_id')
    engine.registry.register(ActionSpec(GITHUB_OPERATION,
        'Inspect bounded GitHub repository, branch and workflow metadata through one exact project connector grant.',
        GitHubInspectionRequest, GitHubInspectionResult, handler, profile='code', workflow='manage-project-sources',
        requires_delta=True, verifier=verify_github_inspection, verification_checks=('github_snapshot_integrity',),
        required_tools=('Python', 'PyGithub'),
        tool_routes=(ToolRoute(GITHUB_OPERATION + '.pygithub', handler, ('Python', 'PyGithub'), extension=extension),)))
