"""Project validation declarations owned by the existing Plan lane.

Adapted from the original acceptance runner's explicit commands and bounds.
Saving configuration does not execute checks, grant tools, or edit sealed tasks.
Repository files and prose never become executable policy through discovery.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from .errors import LaneError
from .migrations import Migration, apply_migrations, read_compatibility
from .redaction import contains_secret, redact_text
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import LaneStore, json_text, now, project_snapshot

SHA256 = r'^[0-9a-f]{64}$'
CHECK_ID = r'^[a-z][a-z0-9_]{0,63}$'
MAX_POLICY_BYTES = 262_144


def digest(value):
    return hashlib.sha256(json_text(value).encode('utf-8')).hexdigest()


def _plain(value):
    if not value.strip() or any(ord(character) < 32 for character in value) or contains_secret(value):
        raise ValueError('Use nonblank, secret-free text without control characters')
    return value


def _relative(value, *, glob=False):
    _plain(value)
    if ('\\' in value or ':' in value or value.startswith('/') or value.endswith('/')
            or any(part in {'', '.', '..'} for part in value.split('/'))
            or (not glob and any(character in value for character in '*?[]'))):
        raise ValueError('Use a normalized project-relative POSIX path without parent traversal')
    return value


class ValidationSelector(Contract):
    """OR within each list; AND across nonempty path/profile/action lists."""
    paths: list[str] = Field(default_factory=lambda: ['**'], min_length=1, max_length=64)
    profiles: list[str] = Field(default_factory=list, max_length=32)
    actions: list[str] = Field(default_factory=list, max_length=64)

    @field_validator('paths')
    @classmethod
    def relative_globs(cls, values):
        if len(set(values)) != len(values) or any(len(value) > 500 for value in values):
            raise ValueError('Use distinct bounded path globs')
        return [_relative(value, glob=True) for value in values]

    @field_validator('profiles', 'actions')
    @classmethod
    def identifiers(cls, values):
        if len(set(values)) != len(values) or any(not re.fullmatch(CHECK_ID, value) for value in values):
            raise ValueError('Use distinct current profile or action IDs')
        return values


class ValidationCommand(Contract):
    """Pinned executor plus literal arguments; no shell expansion or PATH lookup."""
    tool_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z][A-Za-z0-9_.-]*$')
    executable: str = Field(min_length=1, max_length=2000)
    executable_sha256: str = Field(pattern=SHA256)
    arguments: list[str] = Field(default_factory=list, max_length=64)
    working_directory: str = Field(default='.', min_length=1, max_length=500)
    timeout_seconds: int = Field(default=300, ge=1, le=1800)
    max_output_bytes: int = Field(default=8192, ge=256, le=65_536,
        description='Maximum captured bytes in each output stream.')

    @field_validator('tool_id')
    @classmethod
    def tool_identity(cls, value):
        return _plain(value)

    @field_validator('executable')
    @classmethod
    def pinned_executor(cls, value):
        _plain(value)
        windows, posix = PureWindowsPath(value), PurePosixPath(value)
        if not windows.is_absolute() and not posix.is_absolute():
            raise ValueError('The executor must use an explicit absolute path')
        path = windows if windows.is_absolute() else posix
        if ('..' in path.parts or value.startswith(('\\\\', '//'))
                or path.suffix.lower() in {'.cmd', '.bat', '.ps1', '.sh'}
                or path.stem.lower() in {'cmd', 'powershell', 'pwsh', 'sh', 'bash', 'zsh', 'wscript', 'cscript'}):
            raise ValueError('Use a local executable, not a shell, wrapper, share or traversing path')
        return value

    @field_validator('arguments')
    @classmethod
    def literal_arguments(cls, values):
        if sum(len(value.encode('utf-8')) for value in values) > 8192:
            raise ValueError('The argument byte budget is 8192')
        for value in values:
            _plain(value)
            if value in {'&&', '||', ';', '|', '>', '>>', '<', '2>', '&'}:
                raise ValueError('Declare separate checks instead of shell operators')
        return values

    @field_validator('working_directory')
    @classmethod
    def relative_directory(cls, value):
        return value if value == '.' else _relative(value)


class ValidationRule(Contract):
    check_id: str = Field(pattern=CHECK_ID)
    title: str = Field(min_length=1, max_length=300)
    when: ValidationSelector = Field(default_factory=ValidationSelector)
    command: ValidationCommand

    @field_validator('title')
    @classmethod
    def title_text(cls, value):
        return _plain(value)


class ValidationPolicy(Contract):
    """Only additional project checks; owning profile verifiers remain mandatory."""
    title: str = Field(min_length=1, max_length=300)
    checks: list[ValidationRule] = Field(default_factory=list, max_length=64)

    @model_validator(mode='after')
    def bounded_policy(self):
        _plain(self.title)
        if len({check.check_id for check in self.checks}) != len(self.checks):
            raise ValueError('Each validation check ID must occur once')
        if len(self.model_dump_json().encode('utf-8')) > MAX_POLICY_BYTES:
            raise ValueError('The policy exceeds its document byte budget')
        return self


class ValidationPolicySet(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    expected_revision: int = Field(ge=0)
    expected_digest: str | None = Field(default=None, pattern=SHA256)
    policy: ValidationPolicy

    @model_validator(mode='after')
    def exact_head(self):
        if (self.expected_revision == 0) != (self.expected_digest is None):
            raise ValueError('Use revision 0 with no digest initially, otherwise supply both exact head fields')
        return self


class ValidationPolicyRead(Contract):
    revision: int | None = Field(default=None, ge=1)
    expected_digest: str | None = Field(default=None, pattern=SHA256)


class TaskValidationBinding(Contract):
    """Explicit immutable policy and observation scope in one Plan task."""
    policy_revision: int = Field(ge=1)
    policy_digest: str = Field(pattern=SHA256)
    input_paths: list[str] = Field(min_length=1, max_length=128)
    exclude_paths: list[str] = Field(default_factory=list, max_length=64)
    max_files: int = Field(default=2048, ge=1, le=8192)
    max_file_bytes: int = Field(default=8_388_608, ge=1, le=67_108_864)
    max_total_bytes: int = Field(default=67_108_864, ge=1, le=1_073_741_824)
    run_all_checks: bool = False

    @field_validator('input_paths', 'exclude_paths')
    @classmethod
    def bounded_paths(cls, values, info):
        if len(set(values)) != len(values) or any(len(value) > 500 for value in values):
            raise ValueError('Use distinct bounded source paths')
        return [value if value == '.' and info.field_name == 'input_paths'
                else _relative(value, glob=info.field_name == 'exclude_paths') for value in values]


class ValidationPolicySnapshot(Contract):
    project_id: str
    state: Literal['not_configured', 'configured']
    revision: int | None = None
    current_revision: int | None = None
    revision_digest: str | None = None
    policy_digest: str | None = None
    previous_digest: str | None = None
    policy: ValidationPolicy | None = None
    actor_id: str | None = None
    request_id: str | None = None
    receipt_id: str | None = None
    created_at: str | None = None
    authority_lane: Literal['plan'] = 'plan'
    plan_changed: Literal[False] = False
    checks_executed: Literal[False] = False
    runtime_readiness_verified: Literal[False] = False
    profile_verifiers_replaced: Literal[False] = False
    activation: Literal['explicit_new_task_contract_only'] = 'explicit_new_task_contract_only'


VALIDATION_MIGRATIONS = (Migration('validation', 1, 'Project validation policy revisions without implicit task activation', (
    """CREATE TABLE validation_policy_revisions (
        revision INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
        request_digest TEXT NOT NULL, policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
        policy_digest TEXT NOT NULL, previous_digest TEXT, revision_digest TEXT NOT NULL UNIQUE,
        actor_id TEXT NOT NULL, receipt_id TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE validation_policy_current (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        revision INTEGER NOT NULL REFERENCES validation_policy_revisions(revision))""",
)),)

VALIDATION_RUN_MIGRATIONS = (Migration('validationrun', 1, 'Bounded immutable project-check summaries per Delta job', (
    """CREATE TABLE validationrun_summaries (
        job_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, plan_revision INTEGER NOT NULL,
        contract_digest TEXT NOT NULL, summary_object TEXT NOT NULL, receipt_id TEXT NOT NULL UNIQUE)""",
)),)


class ValidationPolicyStore:
    def __init__(self, project):
        self.project = project.project if isinstance(project, LaneStore) else project
        self.lane = self.project.lane('plan')

    def _read(self, connection, request):
        present = connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='validation_policy_current'").fetchone()
        head = connection.execute('SELECT revision FROM validation_policy_current WHERE singleton=1').fetchone() if present else None
        latest = connection.execute('SELECT max(revision) FROM validation_policy_revisions').fetchone()[0] if present else None
        if latest != (head['revision'] if head else None):
            raise LaneError('VALIDATION_POLICY_INTEGRITY', 'The current policy must reference the latest immutable revision.')
        if head is None:
            if request.revision is not None or request.expected_digest is not None:
                raise LaneError('VALIDATION_POLICY_NOT_FOUND', 'This project has no selected validation policy revision.')
            return ValidationPolicySnapshot(project_id=self.project.project_id, state='not_configured')
        revision = request.revision or head['revision']
        row = connection.execute('SELECT * FROM validation_policy_revisions WHERE revision=?', (revision,)).fetchone()
        if row is None:
            raise LaneError('VALIDATION_POLICY_NOT_FOUND', 'Select an existing project validation policy revision.')
        try:
            if len(row['policy_json'].encode('utf-8')) > MAX_POLICY_BYTES:
                raise ValueError('Stored policy exceeds its byte budget')
            raw = json.loads(row['policy_json'])
            policy = ValidationPolicy.model_validate(raw)
            body = {'project_id': self.project.project_id, 'revision': row['revision'],
                'request_id': row['request_id'], 'request_digest': row['request_digest'],
                'policy_digest': row['policy_digest'], 'previous_digest': row['previous_digest'],
                'actor_id': row['actor_id'], 'created_at': row['created_at']}
            valid = policy.model_dump(mode='json') == raw and digest(raw) == row['policy_digest'] and digest(body) == row['revision_digest']
            parent = connection.execute('SELECT revision_digest FROM validation_policy_revisions WHERE revision=?', (revision - 1,)).fetchone()
            valid &= row['previous_digest'] == (parent['revision_digest'] if parent else None) and (revision == 1 or parent is not None)
            if not valid:
                raise ValueError('Policy integrity mismatch')
            with self.project.lane('receipts').connection(read_only=True) as receipts:
                receipt = receipts.execute('SELECT kind,body_json FROM receipts WHERE receipt_id=?', (row['receipt_id'],)).fetchone()
            expected_receipt = {**body, 'revision_digest': row['revision_digest'],
                'checks': [check.check_id for check in policy.checks], 'plan_changed': False, 'checks_executed': False}
            if (receipt is None or receipt['kind'] != 'validation_policy_configured'
                    or json.loads(receipt['body_json']) != expected_receipt):
                raise ValueError('Policy receipt mismatch')
        except (ValueError, TypeError, KeyError):
            raise LaneError('VALIDATION_POLICY_INTEGRITY', 'The stored validation policy revision failed its integrity checks.') from None
        if request.expected_digest is not None and request.expected_digest != row['revision_digest']:
            raise LaneError('VALIDATION_POLICY_STALE', 'Use the exact selected policy revision digest.')
        return ValidationPolicySnapshot(project_id=self.project.project_id, state='configured', revision=revision,
            current_revision=head['revision'], revision_digest=row['revision_digest'], policy_digest=row['policy_digest'],
            previous_digest=row['previous_digest'], policy=policy, actor_id=row['actor_id'], request_id=row['request_id'],
            receipt_id=row['receipt_id'], created_at=row['created_at'])

    def read(self, request=None):
        request = ValidationPolicyRead.model_validate((request or ValidationPolicyRead()).model_dump())
        with project_snapshot(self.project.root):
            read_compatibility(self.lane, VALIDATION_MIGRATIONS)
            with self.lane.connection(read_only=True) as connection:
                return self._read(connection, request)

    def set(self, request, writer, *, actor_id):
        request = ValidationPolicySet.model_validate(request.model_dump())
        if writer.store.root != self.project.root or writer.store.project_id != self.project.project_id:
            raise LaneError('WRITER_PROJECT_MISMATCH', 'The policy writer belongs to another project.')
        if not actor_id or len(actor_id) > 128:
            raise LaneError('VALIDATION_POLICY_ACTOR', 'An authenticated bounded client identity is required.')
        writer.check()
        apply_migrations(self.lane, VALIDATION_MIGRATIONS, writer=writer)
        request_digest = digest(request.model_dump(mode='json'))
        with writer.transaction('plan') as connection:
            existing = connection.execute('SELECT * FROM validation_policy_revisions WHERE request_id=?', (request.request_id,)).fetchone()
            if existing:
                if existing['request_digest'] != request_digest or existing['actor_id'] != actor_id:
                    raise LaneError('VALIDATION_POLICY_REQUEST_CONFLICT', 'This request ID identifies a different policy change.')
                return self._read(connection, ValidationPolicyRead(revision=existing['revision']))
            current = self._read(connection, ValidationPolicyRead())
            if (current.revision or 0) != request.expected_revision or current.revision_digest != request.expected_digest:
                raise LaneError('VALIDATION_POLICY_STALE', 'Read the current project policy before replacing its configuration.')
            policy = request.policy.model_dump(mode='json')
            revision = request.expected_revision + 1
            body = {'project_id': self.project.project_id, 'revision': revision,
                'request_id': request.request_id, 'request_digest': request_digest, 'policy_digest': digest(policy),
                'previous_digest': current.revision_digest, 'actor_id': actor_id, 'created_at': now()}
            revision_digest = digest(body)
            receipt = self.project.append_receipt('validation_policy_configured', {**body,
                'revision_digest': revision_digest, 'checks': [check.check_id for check in request.policy.checks],
                'plan_changed': False, 'checks_executed': False}, connection=connection)
            connection.execute('INSERT INTO validation_policy_revisions VALUES(?,?,?,?,?,?,?,?,?,?)',
                (revision, request.request_id, request_digest, json_text(policy), body['policy_digest'],
                 current.revision_digest, revision_digest, actor_id, receipt, body['created_at']))
            connection.execute('INSERT INTO validation_policy_current VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision', (revision,))
            return self._read(connection, ValidationPolicyRead(revision=revision))


def validate_task_validation(project, task):
    """Validate references without changing the policy, Plan, or source files."""
    binding = task.validation_policy
    if binding is None:
        return None
    snapshot = ValidationPolicyStore(project).read(ValidationPolicyRead(
        revision=binding.policy_revision, expected_digest=binding.policy_digest))
    source = project.source_root
    permitted = [Path(os.path.abspath(source / value)) for value in task.permitted_paths]
    for relative in binding.input_paths:
        path = Path(os.path.abspath(source / relative))
        if not path.is_relative_to(source) or not any(path.is_relative_to(root) for root in permitted if root.is_relative_to(source)):
            raise LaneError('VALIDATION_INPUT_SCOPE', 'Validation inputs must be within the task source-path grant.')
    return snapshot


def _matches(path, pattern):
    return (pattern.endswith('/**') and path == pattern[:-3]) or PurePosixPath(path).full_match(pattern)


def contextual_checks(policy, profile, action):
    return [rule for rule in policy.checks if (not rule.when.profiles or profile in rule.when.profiles)
        and (not rule.when.actions or action in rule.when.actions)]


def select_validation_checks(policy, profile, action, changed_paths, *, run_all=False):
    return [rule for rule in contextual_checks(policy, profile, action)
        if run_all or any(_matches(path, pattern) for path in changed_paths for pattern in rule.when.paths)]


def changed_validation_paths(before, after):
    old, new = before['files'], after['files']
    return {'added': sorted(new.keys() - old.keys()), 'deleted': sorted(old.keys() - new.keys()),
            'modified': sorted(path for path in old.keys() & new.keys() if old[path] != new[path])}


def capture_validation_inputs(guard):
    """Hash the declared regular-file scope; this is not a filesystem snapshot."""
    from .bounded_io import IOBudget, bounded_file_identity
    from .storage import reject_links
    binding = guard.task.validation_policy
    source = guard.store.source_root
    budget = IOBudget(max_file_bytes=binding.max_file_bytes, max_file_count=binding.max_files,
                      max_aggregate_bytes=binding.max_total_bytes)
    files, absent, visited, excluded = {}, [], set(), 0
    stack = list(reversed(binding.input_paths))
    while stack:
        guard.check()
        relative = stack.pop()
        if relative in visited:
            continue
        visited.add(relative)
        if len(visited) > binding.max_files * 8 + 128:
            raise LaneError('VALIDATION_WALK_BUDGET', 'Select a smaller explicit validation input scope.')
        if any(_matches(relative, pattern) for pattern in binding.exclude_paths):
            excluded += 1
            continue
        path = guard.path(relative)
        reject_links(path, source)
        if not path.exists():
            absent.append(relative)
        elif path.is_dir():
            children = []
            with os.scandir(path) as iterator:
                for child in iterator:
                    if len(children) >= binding.max_files * 8 + 128:
                        raise LaneError('VALIDATION_WALK_BUDGET', 'A validation input directory exceeds its enumeration bound.')
                    name = Path(child.path).relative_to(source).as_posix()
                    if len(name) > 2000:
                        raise LaneError('VALIDATION_PATH_BUDGET', 'An observed path exceeds its metadata budget.')
                    children.append(name)
            stack.extend(sorted(children, reverse=True))
        elif path.is_file():
            observed = bounded_file_identity(path, budget=budget, root=source)
            reject_links(path, source)
            files[relative] = {'sha256': observed['sha256'].lower(), 'bytes': observed['size_bytes']}
        else:
            raise LaneError('VALIDATION_REGULAR_FILE_REQUIRED', 'Only regular files and directories can be validation inputs.')
    body = {'schema': 'evidence-lane.validation-input-observation.v4', 'project_id': guard.store.project_id,
        'input_paths': binding.input_paths, 'exclude_paths': binding.exclude_paths, 'files': dict(sorted(files.items())),
        'absent_paths': sorted(absent), 'excluded_entries': excluded, 'bytes_hashed': budget.consumed_bytes,
        'observation_atomic': False, 'dependency_coverage': 'explicit_observation_scope_only'}
    if len(json_text(body).encode()) > 4_194_304:
        raise LaneError('VALIDATION_SNAPSHOT_BUDGET', 'The observed input metadata exceeds four MiB.')
    return body


def _executor_identity(command, source_root):
    from .bounded_io import IOBudget, bounded_file_identity
    from .storage import reject_links
    executable = Path(command.executable)
    if not executable.is_absolute() or not executable.is_file():
        raise LaneError('VALIDATION_EXECUTOR_UNAVAILABLE', 'The pinned executor is unavailable on this host.')
    reject_links(executable, Path(executable.anchor))
    executable = executable.resolve(strict=True)
    if executable.is_relative_to(Path(source_root).resolve()):
        raise LaneError('VALIDATION_EXECUTOR_SOURCE_CONTROLLED', 'Use an external shared executor rather than a project-controlled executable.')
    identity = bounded_file_identity(executable, budget=IOBudget(max_file_bytes=268_435_456,
        max_file_count=1, max_aggregate_bytes=268_435_456))
    if identity['sha256'].lower() != command.executable_sha256:
        raise LaneError('VALIDATION_EXECUTOR_CHANGED', 'The executor bytes differ from the task-bound policy.')
    return str(executable)


def validation_preflight(guard):
    snapshot = validate_task_validation(guard.store, guard.task)
    if snapshot is None:
        return None
    for path in guard.task.validation_policy.input_paths:
        guard.path(path)
    checks = contextual_checks(snapshot.policy, guard.spec.profile, guard.spec.name)
    if checks:
        pool = guard.engine.workers
        if pool is None or not pool.status()['accepting'] or 'validation_command' not in pool.operations:
            raise LaneError('VALIDATION_WORKER_UNAVAILABLE', 'The bound project checks require the current validation worker.')
    for rule in checks:
        if rule.command.tool_id not in guard.task.permitted_tools:
            raise LaneError('VALIDATION_TOOL_SCOPE', 'The task must explicitly grant every potentially selected check tool.')
        readiness = guard.engine.registry.tool_router.observer(rule.command.tool_id, guard.context)
        if readiness.get('ready') is not True:
            raise LaneError('VALIDATION_TOOL_UNAVAILABLE', 'A required validation tool is unavailable.',
                details={'check_id': rule.check_id, 'observation': readiness})
        guard.path(rule.command.working_directory)
        _executor_identity(rule.command, guard.store.source_root)
    return snapshot


def validation_command_worker(arguments):
    """Execute only the parent's exact policy command in the owned process adapter."""
    from .bounded_io import run_owned_bounded_process
    command = ValidationCommand.model_validate(arguments['command'])
    started = time.monotonic()
    result = {'command_digest': digest(command.model_dump(mode='json')), 'check_id': arguments['check_id'],
        'executor_sha256': command.executable_sha256, 'returncode': None, 'status': 'error',
        'stdout': '', 'stderr': '', 'source_root': arguments['source_root'],
        'working_directory': arguments['working_directory'], 'native_host_attested': False,
        'environment_basis': 'bounded_allowlist_without_credentials', 'descendants_joined': False}
    try:
        executor = _executor_identity(command, arguments['source_root'])
        environment = {key: value for key, value in os.environ.items()
            if key.upper() in {'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL'}}
        environment.update(CI='1', GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never')
        observed = run_owned_bounded_process([executor, *command.arguments], cwd=arguments['working_directory'],
            env=environment, timeout_seconds=min(command.timeout_seconds, arguments['timeout_seconds']),
            max_stdout_bytes=command.max_output_bytes, max_stderr_bytes=command.max_output_bytes)
        result.update(returncode=observed.returncode, status='passed' if observed.returncode == 0 else 'failed',
            stdout=redact_text(observed.stdout.decode('utf-8', errors='replace')),
            stderr=redact_text(observed.stderr.decode('utf-8', errors='replace')), descendants_joined=True)
        _executor_identity(command, arguments['source_root'])
    except Exception as error:  # noqa: BLE001 - return finite failure evidence, never vendor messages or inputs
        code = getattr(error, 'code', 'VALIDATION_COMMAND_ERROR')
        public = {'BOUNDED_PROCESS_TIMEOUT': 'timeout', 'BOUNDED_PROCESS_OUTPUT_EXCEEDED': 'output_limit',
                  'VALIDATION_EXECUTOR_CHANGED': 'executor_changed'}
        result.update(status=public.get(code, 'error'), error_code=code if code in public else 'VALIDATION_COMMAND_ERROR')
        # The owned adapter joins its group on normal completion and its bounded failures.
        result['descendants_joined'] |= code in {'BOUNDED_PROCESS_TIMEOUT', 'BOUNDED_PROCESS_OUTPUT_EXCEEDED'}
    result['elapsed_seconds'] = round(time.monotonic() - started, 6)
    return result


def validation_worker_operations():
    from .workers import WorkerOperation
    return (WorkerOperation('validation_command', __name__, 'validation_command_worker',
        path_fields=('working_directory',), max_input_bytes=65_536, max_output_bytes=1_048_576),)


def _observation_content(value):
    return {'files': value['files'], 'absent_paths': value['absent_paths']}


def _read_object(project, object_digest, limit=4_194_304):
    lane = project.lane('plan')
    if lane.object_path(object_digest).stat().st_size > limit:
        raise LaneError('VALIDATION_READ_BUDGET', 'The addressed validation evidence exceeds its read bound.')
    value = json.loads(lane.read_object(object_digest))
    if not isinstance(value, dict):
        raise LaneError('VALIDATION_SUMMARY_INTEGRITY', 'Validation evidence must be a structured object.')
    return value


def record_validation_summary(execution, summary):
    lane = execution.store.lane('plan')
    if len(json_text(summary).encode()) > 131_072:
        raise LaneError('VALIDATION_SUMMARY_BUDGET', 'Use addressed command output and bounded summary rows.')
    with execution.lease.transaction('plan') as connection:
        object_digest = lane.put_object(json_text(summary).encode(), limit=131_072)
        existing = connection.execute('SELECT summary_object FROM validationrun_summaries WHERE job_id=?',
                                      (execution.claim.job_id,)).fetchone()
        if existing:
            if existing['summary_object'] != object_digest:
                raise LaneError('VALIDATION_SUMMARY_CONFLICT', 'This job already has another immutable validation outcome.')
            return object_digest
        fields = {key: summary[key] for key in ('project_id', 'job_id', 'task_id', 'plan_revision', 'contract_digest', 'status')}
        receipt = execution.store.append_receipt('validation_result_recorded',
            {**fields, 'summary_object': object_digest, 'delta_completion_claimed': False}, connection=connection)
        connection.execute('INSERT INTO validationrun_summaries VALUES(?,?,?,?,?,?)',
            (execution.claim.job_id, summary['task_id'], summary['plan_revision'], summary['contract_digest'], object_digest, receipt))
        return object_digest


def read_validation_summary(project, connection, job_id):
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='validationrun_summaries'").fetchone():
        return None
    read_compatibility(project.lane('plan'), VALIDATION_RUN_MIGRATIONS)
    row = connection.execute('SELECT * FROM validationrun_summaries WHERE job_id=?', (job_id,)).fetchone()
    if row is None:
        return None
    value = _read_object(project, row['summary_object'], 131_072)
    expected = {key: row[key] for key in ('job_id', 'task_id', 'plan_revision', 'contract_digest')}
    if value.get('project_id') != project.project_id or any(value.get(key) != item for key, item in expected.items()):
        raise LaneError('VALIDATION_SUMMARY_INTEGRITY', 'The summary belongs to another project or task contract.')
    with project.lane('receipts').connection(read_only=True) as receipts:
        receipt = receipts.execute('SELECT kind,body_json FROM receipts WHERE receipt_id=?', (row['receipt_id'],)).fetchone()
    body = {'project_id': project.project_id, **expected, 'status': value.get('status'),
            'summary_object': row['summary_object'], 'delta_completion_claimed': False}
    if receipt is None or receipt['kind'] != 'validation_result_recorded' or json.loads(receipt['body_json']) != body:
        raise LaneError('VALIDATION_SUMMARY_INTEGRITY', 'The summary and its attributed receipt disagree.')
    return {'summary_object': row['summary_object'], 'receipt_id': row['receipt_id'], 'summary': value}


class DeltaValidation:
    """Optional task-owned CI. The existing Delta exit remains completion owner."""
    def __init__(self, execution, request):
        self.execution, self.guard = execution, execution.guard
        self.policy = validation_preflight(self.guard)
        self.binding = self.guard.task.validation_policy
        self.before = self.after = None
        self.recorded_object = None
        self.summary = {'schema': 'evidence-lane.delta-validation-summary.v4',
            'project_id': execution.store.project_id, 'job_id': execution.claim.job_id,
            'task_id': request.task_id, 'plan_revision': request.plan_revision, 'contract_digest': request.contract_digest,
            'execution_id': execution.claim.execution_id, 'policy_revision': self.binding.policy_revision,
            'policy_digest': self.binding.policy_digest, 'binding_digest': digest(self.binding.model_dump(mode='json')),
            'profile': self.guard.spec.profile, 'action': self.guard.spec.name, 'status': 'running',
            'selection_basis': 'explicit_run_all_checks' if self.binding.run_all_checks else 'observed_source_changes',
            'before_object': None, 'after_action_object': None, 'after_checks_object': None,
            'changes_object': None, 'changed_counts': {}, 'selected_checks': [], 'results': [],
            'inputs_unchanged_during_checks': None, 'source_observation_atomic': False,
            'dependency_coverage': 'explicit_observation_scope_only', 'delta_completion_claimed': False,
            'installed_native_attested': False}

    def _put(self, value, limit=4_194_304):
        with self.execution.lease.transaction('plan'):
            return self.execution.store.lane('plan').put_object(json_text(value).encode(), limit=limit)

    def start(self):
        apply_migrations(self.execution.store.lane('plan'), VALIDATION_RUN_MIGRATIONS, writer=self.execution.lease)
        self.before = capture_validation_inputs(self.guard)
        self.summary['before_object'] = self._put(self.before)

    def finish(self, *, profile_passed):
        self.after = capture_validation_inputs(self.guard)
        self.summary['after_action_object'] = self._put(self.after)
        changes = changed_validation_paths(self.before, self.after)
        paths = sorted(set().union(*[set(values) for values in changes.values()]))
        self.summary.update(changes_object=self._put(changes), changed_counts={key: len(value) for key, value in changes.items()})
        selected = select_validation_checks(self.policy.policy, self.guard.spec.profile, self.guard.spec.name, paths,
                                           run_all=self.binding.run_all_checks)
        self.summary['selected_checks'] = [rule.check_id for rule in selected]
        failed = not profile_passed
        for rule in selected:
            if failed:
                self.summary['results'].append({'check_id': rule.check_id, 'status': 'not_run_after_failure'})
                continue
            self.execution._before_more_work()
            _executor_identity(rule.command, self.execution.store.source_root)
            readiness = self.guard.engine.registry.tool_router.observer(rule.command.tool_id, self.guard.context)
            if readiness.get('ready') is not True:
                raise LaneError('VALIDATION_TOOL_UNAVAILABLE', 'A selected check tool became unavailable before execution.')
            remaining = self.guard.task.budget.max_seconds - (time.monotonic() - self.guard.started) - 1
            if remaining <= 0:
                raise LaneError('DELTA_TIME_BUDGET', 'The task must retain time for its validation summary.')
            effect = self.execution.prepare_effect('validation:' + rule.check_id,
                'Run the exact task-bound project validation command: ' + rule.check_id)
            payload = {'check_id': rule.check_id, 'source_root': str(self.execution.store.source_root),
                'working_directory': str(self.guard.path(rule.command.working_directory)),
                'command': rule.command.model_dump(mode='json'), 'timeout_seconds': min(rule.command.timeout_seconds, remaining)}
            response = self.execution.submit('validation_command', payload).result()
            self.guard.observe(self.execution)
            if response.get('status') != 'ok':
                raise LaneError('VALIDATION_WORKER_FAILED', 'The owned validation worker did not return a measured outcome.')
            measured = response['result']
            if (measured.get('check_id') != rule.check_id or measured.get('command_digest') != digest(payload['command'])
                    or measured.get('executor_sha256') != rule.command.executable_sha256
                    or measured.get('source_root') != payload['source_root']
                    or measured.get('working_directory') != payload['working_directory']):
                raise LaneError('VALIDATION_WORKER_BINDING', 'The worker result differs from its exact policy command.')
            worker_object = self._put(response, limit=1_048_576)
            status = measured['status']
            if status == 'passed' and (measured.get('returncode') != 0 or measured.get('descendants_joined') is not True):
                raise LaneError('VALIDATION_WORKER_BINDING', 'A successful check requires zero exit and joined descendants.')
            self.summary['results'].append({'check_id': rule.check_id, 'status': status,
                'returncode': measured.get('returncode'), 'elapsed_seconds': measured['elapsed_seconds'],
                'worker_object': worker_object, 'worker_digest': digest(response),
                'output_preview': (measured.get('stdout', '') + measured.get('stderr', ''))[-1000:]})
            if measured.get('descendants_joined') is True:
                self.execution.confirm_effect(effect, worker_object)
            after_check = capture_validation_inputs(self.guard)
            unchanged_after_check = _observation_content(after_check) == _observation_content(self.after)
            self.summary['results'][-1].update(after_check_object=self._put(after_check),
                                             inputs_unchanged=unchanged_after_check)
            failed |= status != 'passed' or not unchanged_after_check
        after_checks = capture_validation_inputs(self.guard)
        self.summary['after_checks_object'] = self._put(after_checks)
        unchanged = _observation_content(after_checks) == _observation_content(self.after)
        self.summary['inputs_unchanged_during_checks'] = unchanged
        self.summary['status'] = ('not_run_profile_failed' if not profile_passed else 'failed' if failed or not unchanged
            else 'passed' if selected else 'no_changes' if not paths else 'no_applicable_checks')
        self.recorded_object = record_validation_summary(self.execution, self.summary)
        return not failed and unchanged

    def blocked(self, code):
        if self.recorded_object is not None:
            return
        # No currentness claim is possible after an interrupted boundary. Any
        # completed command rows remain attributed; no skipped work is replayed.
        self.summary.update(status='blocked', error_code=code, inputs_unchanged_during_checks=None)
        self.recorded_object = record_validation_summary(self.execution, self.summary)


def verify_recorded_validation(project, connection, task, verification):
    """Reconcile recorded check evidence; never rerun commands or claim fresh inputs."""
    binding = task.validation_policy
    if binding is None:
        return
    record = read_validation_summary(project, connection, verification['job_id'])
    if record is None or record['summary_object'] != verification.get('project_validation_summary_object'):
        raise LaneError('VALIDATION_SUMMARY_INTEGRITY', 'The bound task requires its exact validation result summary.')
    summary = record['summary']
    policy = validate_task_validation(project, task).policy
    if (summary['binding_digest'] != digest(binding.model_dump(mode='json'))
            or summary['policy_revision'] != binding.policy_revision or summary['policy_digest'] != binding.policy_digest
            or summary['inputs_unchanged_during_checks'] is not True
            or summary['status'] not in {'passed', 'no_changes', 'no_applicable_checks'}
            or any(summary.get(key) != verification.get(key) for key in
                ('job_id', 'task_id', 'plan_revision', 'contract_digest', 'execution_id'))):
        raise LaneError('VALIDATION_SUMMARY_INTEGRITY', 'The recorded policy or check outcome cannot satisfy this task.')
    before = _read_object(project, summary['before_object'])
    after = _read_object(project, summary['after_action_object'])
    final = _read_object(project, summary['after_checks_object'])
    if any(value.get('project_id') != project.project_id or value.get('input_paths') != binding.input_paths
           or value.get('exclude_paths') != binding.exclude_paths for value in (before, after, final)):
        raise LaneError('VALIDATION_SUMMARY_INTEGRITY', 'The observed file scope differs from this task binding.')
    changes = changed_validation_paths(before, after)
    paths = sorted(set().union(*[set(values) for values in changes.values()]))
    selected = select_validation_checks(policy, summary['profile'], summary['action'], paths, run_all=binding.run_all_checks)
    if (summary['profile'] != task.profile or summary['action'] not in task.allowed_actions
            or summary['selected_checks'] != [rule.check_id for rule in selected]
            or [row['check_id'] for row in summary['results']] != summary['selected_checks']
            or _read_object(project, summary['changes_object']) != changes
            or summary['changed_counts'] != {key: len(value) for key, value in changes.items()}
            or _observation_content(after) != _observation_content(final)):
        raise LaneError('VALIDATION_SUMMARY_INTEGRITY', 'The selected checks differ from the recorded input changes.')
    observed_workers = {row['digest'] for row in verification['worker_evidence']}
    for rule, result in zip(selected, summary['results'], strict=True):
        worker = _read_object(project, result['worker_object'], 1_048_576)
        measured = worker.get('result', {})
        if (result['status'] != 'passed' or result.get('inputs_unchanged') is not True
                or _observation_content(_read_object(project, result['after_check_object'])) != _observation_content(after)
                or worker.get('status') != 'ok' or digest(worker) != result['worker_digest']
                or result['worker_digest'] not in observed_workers or measured.get('status') != 'passed'
                or measured.get('returncode') != 0 or measured.get('descendants_joined') is not True
                or measured.get('check_id') != rule.check_id
                or measured.get('command_digest') != digest(rule.command.model_dump(mode='json'))
                or measured.get('executor_sha256') != rule.command.executable_sha256):
            raise LaneError('VALIDATION_SUMMARY_INTEGRITY', 'A selected check lacks its recorded successful worker outcome.')


def register_validation_actions(engine):
    def configure(context, request):
        project = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(project) as writer:
            return ValidationPolicyStore(project).set(request, writer, actor_id=context.client_id)

    engine.registry.register(ActionSpec('validation_policy_set',
        'Save an exact project validation policy revision without executing checks or changing task contracts.',
        ValidationPolicySet, ValidationPolicySnapshot, configure, profile='plan', workflow='plan', permission='write', mutates=True))
    engine.registry.register(ActionSpec('validation_policy_read',
        'Read one project-owned validation policy revision; absence does not inherit plugin CI.',
        ValidationPolicyRead, ValidationPolicySnapshot,
        lambda context, request: ValidationPolicyStore(engine.directory.open(context.project_id)).read(request),
        profile='plan', workflow='plan', queryable_in_delta=True, cross_project_read=True, read_migrations=VALIDATION_MIGRATIONS))
