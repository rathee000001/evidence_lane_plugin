"""Authoritative project Plan revisions and append-only task events.

Adapted from the imported Plan runtime's full task contracts, explicit/linear
dependencies and hash-chained transitions. SQLite now owns the Plan directly;
there is no backlog-JSON authority, PV acceptance or Formula projection.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
from contextlib import nullcontext
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, JsonValue, field_validator, model_serializer, model_validator

from .acceptance import TaskValidationBinding
from .errors import LaneError
from .hashing import atomic_write_bytes
from .migrations import Migration, apply_migrations
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .source_routing import SourceRouteSelection
from .storage import LaneStore, ProjectStore, json_text, now, reject_links
from .writers import WriterLease

TASK_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
TaskState = Literal["queued", "active", "completed", "blocked", "failed", "cancelled", "superseded"]
HostProjectionState = Literal["unbound", "pending", "publishing", "confirmed", "superseded"]
MAX_PLAN_BYTES = 524_288


class TaskBudget(Contract):
    max_seconds: int = Field(default=300, ge=1, le=3600)
    max_tool_calls: int = Field(default=64, ge=1, le=1024)
    max_input_bytes: int = Field(default=1_048_576, ge=1, le=67_108_864)
    max_output_bytes: int = Field(default=4_194_304, ge=1, le=67_108_864)


class TaskModeBinding(Contract):
    """Exact, project-owned classification reference; never ambient session state."""
    classification_id: str = Field(pattern=UUID_PATTERN)
    classification_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_event_id: str = Field(pattern=UUID_PATTERN)
    source_cursor: str = Field(pattern=r'^[0-9a-f]{64}$')
    selection_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    manifest_digest: str = Field(pattern=r'^[0-9a-f]{64}$')


class TaskOperation(Contract):
    """One exact operation owned by this Plan contract, not a second job queue."""
    action: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    source_route: SourceRouteSelection | None = None


class TaskDefinition(Contract):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    title: str = Field(min_length=1, max_length=500)
    requested_outcome: str = Field(min_length=1, max_length=4000)
    profile: str = Field(default="core", pattern=r"^[a-z][a-z0-9_]{0,63}$")
    permitted_paths: list[str] = Field(default_factory=list, max_length=128)
    permitted_tools: list[str] = Field(default_factory=list, max_length=128)
    allowed_actions: list[str] = Field(default_factory=list, max_length=128)
    acceptance_checks: list[str] = Field(default_factory=list, max_length=64)
    stop_condition: str = Field(default="Stop on failed verification or unavailable required capability.", max_length=1000)
    dependencies: list[str] | None = Field(default=None, max_length=64)
    plan_group: str | None = Field(default=None, pattern=TASK_ID_PATTERN)
    commit_batch_id: str | None = Field(default=None, pattern=TASK_ID_PATTERN)
    budget: TaskBudget = Field(default_factory=TaskBudget)
    mode_binding: TaskModeBinding | None = None
    operation: TaskOperation | None = None
    validation_policy: TaskValidationBinding | None = None

    @model_serializer(mode='wrap')
    def serialize_contract(self, handler):
        value = handler(self)
        # Keep pre-binding contracts byte-compatible; no historical task is
        # rewritten merely because an optional explicit reference was added.
        for field in ('mode_binding', 'operation', 'validation_policy'):
            if value.get(field) is None:
                value.pop(field, None)
        return value

    @model_validator(mode='after')
    def operation_scope(self):
        if self.operation is not None:
            if self.operation.action not in self.allowed_actions:
                raise ValueError('The bound operation must be an allowed task action')
            if len(json_text(self.operation.arguments).encode()) > self.budget.max_input_bytes:
                raise ValueError('The bound operation exceeds the task input byte budget')
        return self

    @field_validator("permitted_paths", "permitted_tools", "allowed_actions", "acceptance_checks", "dependencies")
    @classmethod
    def bounded_unique_items(cls, value):
        if value is not None and (len(set(value)) != len(value)
                                  or any(not item.strip() or len(item) > 1000 or "\x00" in item for item in value)):
            raise ValueError("Use distinct, bounded, nonblank entries")
        return value


class PlanCreate(Contract):
    plan_id: str = Field(default_factory=lambda: str(uuid4()), pattern=TASK_ID_PATTERN)
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    title: str = Field(min_length=1, max_length=500)
    source_restore_digest: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    database_recovery_digest: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    tasks: list[TaskDefinition] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def valid_tasks(self):
        seen = set()
        for task in self.tasks:
            if task.task_id in seen:
                raise ValueError("Each task identity must occur once")
            # Retains the base's earlier-row rule. It excludes self references,
            # unknown IDs, cycles and dependencies on later execution rows.
            if task.dependencies is not None and not set(task.dependencies) <= seen:
                raise ValueError("Dependencies must name earlier tasks in the Plan")
            seen.add(task.task_id)
        if len(self.model_dump_json().encode()) > MAX_PLAN_BYTES:
            raise ValueError("The Plan exceeds its document byte budget")
        return self

    def normalized(self) -> list[TaskDefinition]:
        result: list[TaskDefinition] = []
        for task in self.tasks:
            dependencies = task.dependencies
            if dependencies is None:
                dependencies = [result[-1].task_id] if result else []
            result.append(task.model_copy(update={"dependencies": dependencies}))
        return result


class PlanRead(Contract):
    revision: int | None = Field(default=None, ge=1)
    offset: int = Field(default=0, ge=0, le=2000)
    limit: int = Field(default=100, ge=1, le=100)


class PlanReplace(PlanCreate):
    expected_revision: int = Field(ge=1)
    expected_document_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    steer_request_id: str = Field(pattern=UUID_PATTERN)
    resume_after_change: bool = False


class TaskView(Contract):
    position: int
    definition: TaskDefinition
    contract_digest: str
    state: TaskState
    last_event: str | None


class HostPlanBind(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    host_task_id: str = Field(pattern=UUID_PATTERN)
    host_plan_id: str = Field(pattern=UUID_PATTERN)
    plan_path: str = Field(min_length=1, max_length=4096)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class HostPlanProjectionRead(Contract):
    projection_id: str | None = Field(default=None, pattern=UUID_PATTERN)


class HostPlanProjectionSync(Contract):
    projection_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    expected_rows_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class HostPlanProjection(Contract):
    projection_id: str = Field(pattern=UUID_PATTERN)
    state: HostProjectionState
    project_id: str
    plan_id: str
    plan_revision: int = Field(ge=1)
    plan_event_head: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    rows_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: int = Field(ge=1, le=2000)
    rows: list[dict[str, JsonValue]]
    host_task_id: str | None = None
    host_plan_id: str | None = None
    plan_path: str | None = None
    markdown_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    attempts: int = Field(ge=0)
    last_error: str | None = None
    full_list: Literal[True] = True
    partial_window: Literal[False] = False
    project_plan_database_authority: Literal[True] = True
    host_ui_rendering_attested: Literal[False] = False


class HostPlanBinding(Contract):
    binding_id: str = Field(pattern=UUID_PATTERN)
    host_task_id: str = Field(pattern=UUID_PATTERN)
    host_plan_id: str = Field(pattern=UUID_PATTERN)
    plan_path: str
    current_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    projection: HostPlanProjection
    path_selected_by: Literal["explicit_local_binding"] = "explicit_local_binding"
    native_task_attestation: Literal["not_provided"] = "not_provided"


class PlanSnapshot(Contract):
    state: Literal["no_plan", "ready"]
    project_id: str
    plan_id: str | None = None
    title: str | None = None
    revision: int | None = None
    current_revision: int | None = None
    document_digest: str | None = None
    total_tasks: int = 0
    offset: int = 0
    truncated: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    tasks: list[TaskView] = Field(default_factory=list)
    event_head: str | None = None
    authority: Literal["project_sqlite"] = "project_sqlite"
    host_projection: HostPlanProjection | None = None


PLAN_MIGRATIONS = (Migration("plan", 1, "Current Plan, immutable revisions and task-event history", (
    """CREATE TABLE plan_revisions (
        revision INTEGER PRIMARY KEY, plan_id TEXT NOT NULL, title TEXT NOT NULL,
        request_id TEXT NOT NULL UNIQUE, document_json TEXT NOT NULL CHECK(json_valid(document_json)),
        document_digest TEXT NOT NULL, parent_revision INTEGER REFERENCES plan_revisions(revision),
        actor_id TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE plan_current (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        revision INTEGER NOT NULL REFERENCES plan_revisions(revision), event_head TEXT)""",
    """CREATE TABLE plan_tasks (
        revision INTEGER NOT NULL REFERENCES plan_revisions(revision), task_id TEXT NOT NULL,
        position INTEGER NOT NULL, definition_json TEXT NOT NULL CHECK(json_valid(definition_json)),
        contract_digest TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN
          ('queued','active','completed','blocked','failed','cancelled','superseded')),
        last_event TEXT, PRIMARY KEY(revision,task_id), UNIQUE(revision,position))""",
    "CREATE UNIQUE INDEX plan_one_active ON plan_tasks(revision) WHERE state='active'",
    """CREATE TABLE plan_dependencies (
        revision INTEGER NOT NULL, task_id TEXT NOT NULL, dependency_id TEXT NOT NULL,
        PRIMARY KEY(revision,task_id,dependency_id),
        FOREIGN KEY(revision,task_id) REFERENCES plan_tasks(revision,task_id),
        FOREIGN KEY(revision,dependency_id) REFERENCES plan_tasks(revision,task_id))""",
    """CREATE TABLE plan_events (
        sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
        revision INTEGER NOT NULL REFERENCES plan_revisions(revision), task_id TEXT,
        kind TEXT NOT NULL, actor_id TEXT NOT NULL, payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        previous_digest TEXT, digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)""",
    "CREATE INDEX plan_task_events ON plan_events(revision,task_id,sequence)",
)), Migration("plan", 2, "Historical task identity lookup", (
    "CREATE INDEX plan_task_identity ON plan_tasks(task_id)",
)), Migration("plan", 3, "Exact full Plan projection to one explicitly bound Codex host Plan", (
    """CREATE TABLE plan_host_bindings (
        binding_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
        host_task_id TEXT NOT NULL, host_plan_id TEXT NOT NULL, plan_path TEXT NOT NULL,
        current_sha256 TEXT NOT NULL CHECK(length(current_sha256)=64),
        bound_by TEXT NOT NULL, binding_digest TEXT NOT NULL UNIQUE,
        active INTEGER NOT NULL CHECK(active IN (0,1)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE UNIQUE INDEX plan_one_active_host_binding ON plan_host_bindings(active) WHERE active=1",
    """CREATE TABLE plan_host_projections (
        sequence INTEGER PRIMARY KEY, projection_id TEXT NOT NULL UNIQUE,
        binding_id TEXT REFERENCES plan_host_bindings(binding_id),
        plan_revision INTEGER NOT NULL REFERENCES plan_revisions(revision),
        plan_event_head TEXT NOT NULL, document_digest TEXT NOT NULL,
        rows_json TEXT NOT NULL CHECK(json_valid(rows_json)),
        rows_digest TEXT NOT NULL CHECK(length(rows_digest)=64),
        markdown_text TEXT NOT NULL, markdown_sha256 TEXT NOT NULL CHECK(length(markdown_sha256)=64),
        state TEXT NOT NULL CHECK(state IN ('unbound','pending','publishing','confirmed','superseded')),
        previous_projection_id TEXT, prepared_by TEXT NOT NULL, prepared_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
        file_before_sha256 TEXT, file_after_sha256 TEXT,
        confirmed_at TEXT)""",
    "CREATE INDEX plan_host_projection_source ON plan_host_projections(plan_revision,plan_event_head,sequence)",
)))


def content_digest(value) -> str:
    return hashlib.sha256(json_text(value).encode()).hexdigest()


class PlanStore:
    def __init__(self, store: ProjectStore):
        self.project = store.project if isinstance(store, LaneStore) else store
        self.store = self.project.lane('plan')

    def _lease(self, lease: WriterLease):
        if lease.store.project_id != self.store.project_id or lease.store.root != self.store.root:
            raise LaneError("WRITER_PROJECT_MISMATCH", "The Plan writer belongs to another project.")
        lease.check()

    def initialize(self, lease: WriterLease):
        self._lease(lease)
        apply_migrations(self.store, PLAN_MIGRATIONS, writer=lease)

    def _validate_modes(self, tasks):
        from .acceptance import validate_task_validation
        from .mode_governance import validate_task_mode_binding
        for task in tasks:
            if task.mode_binding is not None:
                validate_task_mode_binding(self.project, task)
            if task.validation_policy is not None:
                validate_task_validation(self.project, task)

    @staticmethod
    def _native_state(state: TaskState) -> str:
        return {'completed': 'completed', 'active': 'in_progress'}.get(state, 'pending')

    def _projection_rows(self, connection, revision: int) -> list[dict[str, JsonValue]]:
        rows = []
        for row in connection.execute(
            'SELECT * FROM plan_tasks WHERE revision=? ORDER BY position', (revision,)
        ):
            view = self._view(row, connection=connection)
            rows.append({
                'position': view.position,
                'task_id': view.definition.task_id,
                'title': view.definition.title,
                'requested_outcome': view.definition.requested_outcome,
                'state': view.state,
                'host_status': self._native_state(view.state),
                'contract_digest': view.contract_digest,
                'definition': view.definition.model_dump(mode='json'),
            })
        if not rows or len(rows) > 2000:
            raise LaneError('PLAN_PROJECTION_ROWS', 'A host Plan projection requires the complete bounded task list.')
        if sum(row['state'] == 'active' for row in rows) > 1:
            raise LaneError('PLAN_PROJECTION_ACTIVE', 'A host Plan projection permits at most one active task.')
        return rows

    @staticmethod
    def _markdown_cell(value) -> str:
        text = html.escape(str(value), quote=False).replace('\\', '\\\\')
        for marker in ('|', '`', '*', '_', '[', ']'):
            text = text.replace(marker, '\\' + marker)
        return text.replace('\r', ' ').replace('\n', '<br>')

    def _projection_markdown(self, metadata, event_head: str, rows, rows_digest: str) -> str:
        counts = {state: sum(row['state'] == state for row in rows) for state in (
            'completed', 'active', 'queued', 'blocked', 'failed', 'cancelled', 'superseded')}
        lines = [
            '# Evidence Lane project Plan', '',
            f'- Project ID: `{self.project.project_id}`',
            f'- Plan ID: `{metadata["plan_id"]}`',
            f'- Plan revision: `{metadata["revision"]}`',
            f'- Plan document SHA-256: `{metadata["document_digest"]}`',
            f'- Plan event head: `{event_head}`',
            f'- Full ordered-row SHA-256: `{rows_digest}`',
            f'- Full row count: `{len(rows)}`',
            '- Authority: the selected project Plan lane database. This file is its exact full host projection.',
            '- Completed contracts remain historical; one task may be active; affected active or queued contracts change only through a new Plan event or revision.',
            '- No partial 1 + 9 projection is used. Informational reads do not rewrite this file.', '',
            '## Status counts', '',
            *[f'- {state}: `{count}`' for state, count in counts.items() if count], '',
            '## Full Step Task List', '',
            '| Row | Task ID | Project state | Host state | Current task contract |',
            '|---:|---|---|---|---|',
        ]
        for row in rows:
            task = f'{row["title"]}: {row["requested_outcome"]}'
            lines.append(
                f'| {row["position"]:03d} | `{self._markdown_cell(row["task_id"])}` | '
                f'**{row["state"]}** | `{row["host_status"]}` | {self._markdown_cell(task)} |'
            )
        lines.extend(['', '## Projection contract', '',
            '- This file is written only through an explicitly bound local Codex Plan path.',
            '- The project database commit precedes atomic file replacement.',
            '- File and database digests are reconciled before the projection is confirmed.',
            '- File parity does not independently attest that a particular Codex window rendered it.', ''])
        result = '\n'.join(lines)
        if len(result.encode('utf-8')) > MAX_PLAN_BYTES * 3:
            raise LaneError('PLAN_PROJECTION_TOO_LARGE', 'The complete host Plan projection exceeds its file budget.')
        return result

    def _prepare_host_projection(self, connection, *, revision: int, actor_id: str) -> str:
        head = self._head(connection, revision)
        if not head['event_head']:
            raise LaneError('PLAN_PROJECTION_EVENT_HEAD', 'Record the Plan mutation before preparing its host projection.')
        metadata = connection.execute('SELECT * FROM plan_revisions WHERE revision=?', (revision,)).fetchone()
        self._validate_document(metadata)
        rows = self._projection_rows(connection, revision)
        rows_digest = content_digest(rows)
        markdown = self._projection_markdown(metadata, head['event_head'], rows, rows_digest)
        markdown_sha256 = hashlib.sha256(markdown.encode('utf-8')).hexdigest()
        binding = connection.execute(
            'SELECT * FROM plan_host_bindings WHERE active=1'
        ).fetchone()
        previous = connection.execute(
            'SELECT * FROM plan_host_projections ORDER BY sequence DESC LIMIT 1'
        ).fetchone()
        if (previous and previous['plan_revision'] == revision
                and previous['plan_event_head'] == head['event_head']
                and previous['rows_digest'] == rows_digest
                and previous['binding_id'] == (binding['binding_id'] if binding else None)
                and previous['state'] != 'superseded'):
            return previous['projection_id']
        connection.execute(
            "UPDATE plan_host_projections SET state='superseded' "
            "WHERE state IN ('unbound','pending','publishing')"
        )
        projection_id = str(uuid4())
        sequence = connection.execute(
            'SELECT coalesce(max(sequence),0)+1 FROM plan_host_projections'
        ).fetchone()[0]
        connection.execute(
            'INSERT INTO plan_host_projections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (sequence, projection_id, binding['binding_id'] if binding else None,
             revision, head['event_head'], metadata['document_digest'], json_text(rows),
             rows_digest, markdown, markdown_sha256, 'pending' if binding else 'unbound',
             previous['projection_id'] if previous else None, actor_id, now(), 0, None,
             None, None, None)
        )
        return projection_id

    @staticmethod
    def _file_sha256(path: Path) -> str:
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()

    @staticmethod
    def _codex_plan_root() -> Path:
        selected = os.environ.get('CODEX_HOME')
        return (Path(selected) if selected else Path.home() / '.codex') / 'plans'

    def _validated_host_plan_path(self, binding) -> Path:
        root = self._codex_plan_root()
        path = Path(binding['plan_path'])
        if not path.is_absolute() or path.name != 'PLAN.md':
            raise LaneError('HOST_PLAN_PATH_INVALID', 'Bind an absolute Codex PLAN.md path.')
        try:
            relative = path.relative_to(root)
        except ValueError:
            raise LaneError('HOST_PLAN_PATH_INVALID', 'The host Plan must remain under the current Codex plans root.') from None
        if (len(relative.parts) != 3 or relative.parts[0] != binding['host_task_id']
                or relative.parts[1] != binding['host_plan_id'] or relative.parts[2] != 'PLAN.md'):
            raise LaneError('HOST_PLAN_BINDING_MISMATCH', 'The path does not match its exact task and Plan identities.')
        reject_links(path, root)
        if not path.is_file():
            raise LaneError('HOST_PLAN_NOT_FOUND', 'The explicitly selected Codex PLAN.md does not exist.')
        return path

    def _projection_result(self, connection, row) -> HostPlanProjection:
        try:
            rows = json.loads(row['rows_json'])
            if (content_digest(rows) != row['rows_digest']
                    or hashlib.sha256(row['markdown_text'].encode('utf-8')).hexdigest() != row['markdown_sha256']):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise LaneError('PLAN_PROJECTION_INTEGRITY', 'The stored host Plan projection differs from its digests.') from None
        metadata = connection.execute(
            'SELECT * FROM plan_revisions WHERE revision=?', (row['plan_revision'],)
        ).fetchone()
        self._validate_document(metadata)
        binding = (connection.execute('SELECT * FROM plan_host_bindings WHERE binding_id=?',
                                      (row['binding_id'],)).fetchone()
                   if row['binding_id'] else None)
        return HostPlanProjection(
            projection_id=row['projection_id'], state=row['state'],
            project_id=self.project.project_id, plan_id=metadata['plan_id'],
            plan_revision=row['plan_revision'], plan_event_head=row['plan_event_head'],
            document_digest=row['document_digest'], rows_digest=row['rows_digest'],
            row_count=len(rows), rows=rows,
            host_task_id=binding['host_task_id'] if binding else None,
            host_plan_id=binding['host_plan_id'] if binding else None,
            plan_path=binding['plan_path'] if binding else None,
            markdown_sha256=row['markdown_sha256'], file_sha256=row['file_after_sha256'],
            attempts=row['attempts'], last_error=row['last_error'])

    def host_projection(self, request: HostPlanProjectionRead | None = None) -> HostPlanProjection:
        request = request or HostPlanProjectionRead()
        with self.store.connection(read_only=True) as connection:
            if not connection.execute(
                    "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='plan_host_projections'").fetchone():
                raise LaneError('PLAN_PROJECTION_NOT_FOUND', 'No project Plan mutation has prepared a host projection.')
            row = (connection.execute('SELECT * FROM plan_host_projections WHERE projection_id=?',
                                      (request.projection_id,)).fetchone()
                   if request.projection_id else connection.execute(
                       'SELECT * FROM plan_host_projections ORDER BY sequence DESC LIMIT 1').fetchone())
            if row is None:
                raise LaneError('PLAN_PROJECTION_NOT_FOUND', 'No project Plan mutation has prepared a host projection.')
            return self._projection_result(connection, row)

    def bind_host_plan(self, request: HostPlanBind, lease: WriterLease, *, actor_id: str) -> HostPlanBinding:
        self._lease(lease)
        self.initialize(lease)
        candidate = {
            'host_task_id': request.host_task_id, 'host_plan_id': request.host_plan_id,
            'plan_path': str(Path(request.plan_path)),
        }
        binding_body = {**candidate, 'request_id': request.request_id,
                        'project_id': self.project.project_id, 'bound_by': actor_id}
        binding_digest = content_digest(binding_body)
        with self.store.connection(read_only=True) as connection:
            repeated = connection.execute(
                'SELECT * FROM plan_host_bindings WHERE request_id=?', (request.request_id,)
            ).fetchone()
        if repeated:
            if repeated['binding_digest'] != binding_digest:
                raise LaneError('HOST_PLAN_BINDING_CONFLICT', 'This request ID identifies another host Plan binding.')
            path = self._validated_host_plan_path(repeated)
            if self._file_sha256(path) != repeated['current_sha256']:
                raise LaneError('HOST_PLAN_CHANGED', 'The previously bound host Plan changed after its last projection.')
        else:
            path = self._validated_host_plan_path(candidate)
            current_sha256 = self._file_sha256(path)
            if current_sha256 != request.expected_sha256:
                raise LaneError('HOST_PLAN_CHANGED', 'The selected host Plan differs from the expected pre-binding hash.')
        with lease.transaction('plan') as connection:
            existing = connection.execute(
                'SELECT * FROM plan_host_bindings WHERE request_id=?', (request.request_id,)
            ).fetchone()
            if existing:
                if existing['binding_digest'] != binding_digest:
                    raise LaneError('HOST_PLAN_BINDING_CONFLICT', 'This request ID identifies another host Plan binding.')
                binding_id = existing['binding_id']
                connection.execute('UPDATE plan_host_bindings SET active=0,updated_at=? WHERE binding_id!=?',
                                   (now(), binding_id))
                connection.execute('UPDATE plan_host_bindings SET active=1,updated_at=? WHERE binding_id=?',
                                   (now(), binding_id))
            else:
                binding_id = str(uuid4())
                connection.execute('UPDATE plan_host_bindings SET active=0,updated_at=? WHERE active=1', (now(),))
                connection.execute('INSERT INTO plan_host_bindings VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (binding_id, request.request_id, request.host_task_id, request.host_plan_id,
                     str(path), current_sha256, actor_id, binding_digest, 1, now(), now()))
            head = self._head(connection)
            projection_id = self._prepare_host_projection(
                connection, revision=head['revision'], actor_id=actor_id)
        projection = self.sync_host_plan(
            HostPlanProjectionSync(projection_id=projection_id), lease, actor_id=actor_id)
        return HostPlanBinding(binding_id=binding_id, host_task_id=request.host_task_id,
            host_plan_id=request.host_plan_id, plan_path=str(path),
            current_sha256=projection.file_sha256, projection=projection)

    def sync_host_plan(self, request: HostPlanProjectionSync, lease: WriterLease, *, actor_id: str) -> HostPlanProjection:
        self._lease(lease)
        self.initialize(lease)
        error = None
        with lease.transaction('plan') as connection:
            row = (connection.execute('SELECT * FROM plan_host_projections WHERE projection_id=?',
                                      (request.projection_id,)).fetchone()
                   if request.projection_id else connection.execute(
                       "SELECT * FROM plan_host_projections WHERE state IN ('pending','publishing') "
                       'ORDER BY sequence DESC LIMIT 1').fetchone())
            if row is None:
                raise LaneError('PLAN_PROJECTION_NOT_FOUND', 'No pending full host Plan projection exists.')
            self._projection_result(connection, row)
            if request.expected_rows_digest and request.expected_rows_digest != row['rows_digest']:
                raise LaneError('PLAN_PROJECTION_CHANGED', 'The pending full Plan rows differ from the selected digest.')
            head = self._head(connection, row['plan_revision'])
            if head['event_head'] != row['plan_event_head']:
                raise LaneError('PLAN_PROJECTION_SUPERSEDED', 'A newer project Plan mutation superseded this projection.')
            binding = connection.execute(
                'SELECT * FROM plan_host_bindings WHERE binding_id=? AND active=1', (row['binding_id'],)
            ).fetchone() if row['binding_id'] else None
            if binding is None:
                raise LaneError('HOST_PLAN_NOT_BOUND', 'Bind the exact current Codex Plan before projection.')
            path = self._validated_host_plan_path(binding)
            actual = self._file_sha256(path)
            if row['state'] == 'confirmed':
                if actual != row['markdown_sha256'] or binding['current_sha256'] != actual:
                    raise LaneError('HOST_PLAN_CHANGED', 'The confirmed host Plan changed after projection.')
                return self._projection_result(connection, row)
            if actual not in {binding['current_sha256'], row['markdown_sha256']}:
                error = 'HOST_PLAN_CHANGED'
                connection.execute(
                    "UPDATE plan_host_projections SET state='pending',attempts=attempts+1,last_error=? "
                    'WHERE projection_id=?', (error, row['projection_id']))
            else:
                connection.execute(
                    "UPDATE plan_host_projections SET state='publishing',attempts=attempts+1,last_error=NULL,"
                    'file_before_sha256=? WHERE projection_id=?', (actual, row['projection_id']))
                markdown = row['markdown_text'].encode('utf-8')
                projection_id = row['projection_id']
        if error:
            raise LaneError(error, 'The exact bound Codex Plan changed outside this project Plan projection.')
        try:
            if actual != hashlib.sha256(markdown).hexdigest():
                atomic_write_bytes(path, markdown)
            after = self._file_sha256(path)
            if after != hashlib.sha256(markdown).hexdigest():
                raise LaneError('HOST_PLAN_WRITE_MISMATCH', 'The linked Codex Plan readback differs from the full projection.')
        except (OSError, LaneError) as failure:
            with lease.transaction('plan') as connection:
                connection.execute(
                    "UPDATE plan_host_projections SET state='pending',last_error=? WHERE projection_id=?",
                    (failure.code if isinstance(failure, LaneError) else 'HOST_PLAN_WRITE_FAILED', projection_id))
            if isinstance(failure, LaneError):
                raise
            raise LaneError('HOST_PLAN_WRITE_FAILED', 'The linked Codex Plan could not be replaced atomically.') from None
        with lease.transaction('plan') as connection:
            connection.execute(
                "UPDATE plan_host_projections SET state='confirmed',file_after_sha256=?,confirmed_at=?,last_error=NULL "
                'WHERE projection_id=?', (after, now(), projection_id))
            connection.execute(
                'UPDATE plan_host_bindings SET current_sha256=?,updated_at=? WHERE binding_id=(SELECT binding_id '
                'FROM plan_host_projections WHERE projection_id=?)', (after, now(), projection_id))
            self.store.append_receipt('host_plan_projection_confirmed', {
                'projection_id': projection_id, 'rows_digest': row['rows_digest'],
                'file_sha256': after, 'full_list': True, 'partial_window': False,
                'host_ui_rendering_attested': False, 'project_plan_database_authority': True,
                'projected_by': actor_id}, connection=connection)
            confirmed = connection.execute(
                'SELECT * FROM plan_host_projections WHERE projection_id=?', (projection_id,)
            ).fetchone()
            return self._projection_result(connection, confirmed)

    def _publish_if_bound(self, projection_id: str, lease: WriterLease, *, actor_id: str) -> HostPlanProjection:
        projection = self.host_projection(HostPlanProjectionRead(projection_id=projection_id))
        if projection.state in {'pending', 'publishing'}:
            return self.sync_host_plan(
                HostPlanProjectionSync(
                    projection_id=projection_id,
                    expected_rows_digest=projection.rows_digest,
                ),
                lease,
                actor_id=actor_id,
            )
        return projection

    @staticmethod
    def _head(connection, expected_revision: int | None = None):
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='plan_current' AND type='table'").fetchone():
            raise LaneError("PLAN_NOT_CREATED", "Create this project's Plan first.")
        row = connection.execute("SELECT * FROM plan_current WHERE singleton=1").fetchone()
        if row is None:
            raise LaneError("PLAN_NOT_CREATED", "Create this project's Plan first.")
        if expected_revision is not None and row["revision"] != expected_revision:
            raise LaneError("STALE_PLAN_REVISION", "Refresh the Plan before changing or executing this task.")
        return row

    @staticmethod
    def _task(connection, revision: int, task_id: str):
        row = connection.execute("SELECT * FROM plan_tasks WHERE revision=? AND task_id=?", (revision, task_id)).fetchone()
        if row is None:
            raise LaneError("PLAN_TASK_NOT_FOUND", "Select a task from the pinned Plan revision.")
        return row

    def _event(self, connection, *, revision: int, task_id: str | None, kind: str,
               actor_id: str, payload: dict, event_id: str | None = None) -> str:
        if not actor_id or len(actor_id) > 128:
            raise LaneError("PLAN_ACTOR_REQUIRED", "A bounded engine-authenticated actor is required.")
        head = self._head(connection)
        sequence = connection.execute("SELECT coalesce(max(sequence),0)+1 FROM plan_events").fetchone()[0]
        body = {"sequence": sequence, "event_id": event_id or str(uuid4()), "revision": revision,
                "task_id": task_id, "kind": kind, "actor_id": actor_id, "payload": payload,
                "previous_digest": head["event_head"], "created_at": now()}
        digest = content_digest(body)
        connection.execute("INSERT INTO plan_events VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (sequence, body["event_id"], revision, task_id, kind, actor_id, json_text(payload),
                            body["previous_digest"], digest, body["created_at"]))
        connection.execute("UPDATE plan_current SET event_head=? WHERE singleton=1", (digest,))
        if task_id is not None:
            connection.execute("UPDATE plan_tasks SET last_event=? WHERE revision=? AND task_id=?",
                               (digest, revision, task_id))
        return digest

    def create(self, request: PlanCreate, lease: WriterLease, *, actor_id: str) -> PlanSnapshot:
        # Revalidate even a model_copy; mutable nested lists never bypass contracts.
        request = PlanCreate.model_validate(request.model_dump())
        self._lease(lease)
        self._validate_modes(request.tasks)
        document = request.model_dump(mode="json")
        document["tasks"] = [task.model_dump(mode="json") for task in request.normalized()]
        for field in ('source_restore_digest', 'database_recovery_digest'):
            if document[field] is None:
                document.pop(field)
        digest = content_digest(document)
        if len(json_text(document).encode()) > MAX_PLAN_BYTES:
            raise LaneError("PLAN_TOO_LARGE", "The normalized Plan exceeds its document byte budget.")
        self.initialize(lease)
        with lease.coordinated_transaction(['plan', 'sources']) as commit:
            connection = commit.connection('plan')
            existing = connection.execute("SELECT * FROM plan_revisions WHERE request_id=?", (request.request_id,)).fetchone()
            if existing:
                if existing["document_digest"] != digest or existing["actor_id"] != actor_id:
                    raise LaneError("PLAN_REQUEST_CONFLICT", "This request ID already identifies a different Plan change.")
            else:
                if connection.execute("SELECT 1 FROM plan_current").fetchone():
                    raise LaneError("PLAN_ALREADY_CREATED", "Replace the existing Plan through a revision-safe steer.")
                from .store import require_restoration_plan
                require_restoration_plan(commit.connection('sources'), request.source_restore_digest, 1)
                from .database_recovery import require_recovery_plan
                require_recovery_plan(commit.connection('receipts'), request.database_recovery_digest, 1)
                connection.execute("INSERT INTO plan_revisions VALUES(1,?,?,?,?,?,NULL,?,'initial_plan',?)",
                                   (request.plan_id, request.title, request.request_id, json_text(document), digest, actor_id, now()))
                connection.execute("INSERT INTO plan_current VALUES(1,1,NULL)")
                self._insert_tasks(connection, 1, request.normalized())
                self._event(connection, revision=1, task_id=None, kind="plan_created", actor_id=actor_id,
                            payload={"document_digest": digest, "task_count": len(request.tasks)})
                self.store.append_receipt("plan_created", {"plan_id": request.plan_id, "revision": 1,
                                          "document_digest": digest}, connection=connection)
            projection_id = self._prepare_host_projection(
                connection, revision=existing['revision'] if existing else 1, actor_id=actor_id)
        self._publish_if_bound(projection_id, lease, actor_id=actor_id)
        return self.snapshot(PlanRead(revision=1))

    @staticmethod
    def _insert_tasks(connection, revision: int, tasks: list[TaskDefinition]):
        for position, task in enumerate(tasks, 1):
            value = task.model_dump(mode="json")
            connection.execute("INSERT INTO plan_tasks VALUES(?,?,?,?,?,'queued',NULL)",
                               (revision, task.task_id, position, json_text(value), content_digest(value)))
            connection.executemany("INSERT INTO plan_dependencies VALUES(?,?,?)",
                                   [(revision, task.task_id, dependency) for dependency in task.dependencies or []])

    def replace(self, request: PlanReplace, lease: WriterLease, *, actor_id: str) -> PlanSnapshot:
        """Commit task contracts, supersession and the current head atomically.

        The caller holds the coordinator's ordinary mutation scope, which cannot
        coexist with an engine-owned execution. SQLite checks additionally reject
        active/unreconciled jobs and an uncheckpointed active Plan row.
        """
        from .lineage import ChatLineage
        request = PlanReplace.model_validate(request.model_dump())
        self._lease(lease)
        tasks = request.normalized()
        document = {**request.model_dump(mode="json"), "tasks": [task.model_dump(mode="json") for task in tasks]}
        for field in ('source_restore_digest', 'database_recovery_digest'):
            if document[field] is None:
                document.pop(field)
        if len(json_text(document).encode()) > MAX_PLAN_BYTES:
            raise LaneError("PLAN_TOO_LARGE", "The revised Plan exceeds its document byte budget.")
        digest = content_digest(document)
        self.initialize(lease)
        with lease.coordinated_transaction(['plan', 'chat_lineage', 'sources']) as commit:
            connection = commit.connection('plan')
            lineage = commit.connection('chat_lineage')
            prior_request = connection.execute("SELECT * FROM plan_revisions WHERE request_id=?", (request.request_id,)).fetchone()
            if prior_request:
                if prior_request["document_digest"] != digest or prior_request["actor_id"] != actor_id:
                    raise LaneError("PLAN_REQUEST_CONFLICT", "This request ID already identifies a different Plan revision.")
                revision = prior_request["revision"]
            else:
                self._head(connection, request.expected_revision)
                previous = connection.execute("SELECT * FROM plan_revisions WHERE revision=?", (request.expected_revision,)).fetchone()
                self._validate_document(previous)
                if previous["plan_id"] != request.plan_id or previous["document_digest"] != request.expected_document_digest:
                    raise LaneError("PLAN_SOURCE_CHANGED", "The selected Plan identity or source digest no longer matches.")
                if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='steer_requests'").fetchone():
                    raise LaneError("SEMANTIC_STEER_REQUIRED", "Bind the revision to a recorded semantic steer first.")
                steer = connection.execute("SELECT * FROM steer_requests WHERE request_id=?", (request.steer_request_id,)).fetchone()
                if (steer is None or steer["intent"] != "semantic" or steer["expected_revision"] != request.expected_revision
                        or steer["actor_id"] != actor_id or steer["state"] not in {"pending", "checkpointing", "ready"}):
                    raise LaneError("SEMANTIC_STEER_REQUIRED", "Use this client's current, unapplied semantic steer.")
                source = lineage.execute("SELECT * FROM lineage_events WHERE event_id=?", (steer["source_event_id"],)).fetchone()
                if source is None or source["cursor"] != steer["source_cursor"] or source["client_id"] != actor_id:
                    raise LaneError("STEER_SOURCE_MISMATCH", "The recorded steer no longer matches its captured input.")
                ChatLineage.validate_row(source)
                self.project.lane('chat_lineage').read_object(source["payload_digest"])
                pending_sources = connection.execute("SELECT source_event_id FROM steer_requests "
                    "WHERE expected_revision=? AND state IN ('pending','checkpointing','ready') LIMIT 1001",
                    (request.expected_revision,)).fetchall()
                if len(pending_sources) > 1000:
                    raise LaneError('STEER_BACKLOG_BUDGET', 'Reconcile the steer backlog before replacing the Plan.')
                newer = any(lineage.execute('SELECT 1 FROM lineage_events WHERE event_id=? AND sequence>?',
                            (item['source_event_id'], source['sequence'])).fetchone() for item in pending_sources)
                if newer:
                    raise LaneError("NEWER_STEER_PENDING", "Build the current revision from the newer captured steer.")
                control = (connection.execute("SELECT * FROM steer_control WHERE singleton=1").fetchone()
                           if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='steer_control'").fetchone() else None)
                if control and control["paused"]:
                    stop_source = lineage.execute("SELECT sequence FROM lineage_events WHERE event_id=?", (control["source_event_id"],)).fetchone()
                    if stop_source is None or source["sequence"] <= stop_source["sequence"]:
                        raise LaneError("PROJECT_PAUSED", "A later visible instruction is required after the recorded stop.")
                old = {row["task_id"]: row for row in connection.execute("SELECT * FROM plan_tasks WHERE revision=? ORDER BY position",
                                                                         (request.expected_revision,))}
                if any(row["state"] == "active" for row in old.values()):
                    raise LaneError("PLAN_CHECKPOINT_REQUIRED", "Checkpoint the active task before replacing its revision.")
                has_jobs = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='jobs_jobs'").fetchone() is not None
                if has_jobs and connection.execute("SELECT 1 FROM jobs_jobs WHERE state IN ('running','uncertain') LIMIT 1").fetchone():
                    raise LaneError("JOBS_NOT_QUIESCENT", "Finish or reconcile the project's active and uncertain jobs first.")
                if steer["checkpoint_object"]:
                    checkpoint = json.loads(self.store.read_object(steer["checkpoint_object"]))
                    if checkpoint.get("plan_revision") != request.expected_revision:
                        raise LaneError("STEER_CHECKPOINT_MISMATCH", "The safe checkpoint belongs to another Plan revision.")
                replacements = {task.task_id: task for task in tasks}
                terminal = {"completed", "cancelled", "failed", "superseded"}
                changed = set()
                for task_id, row in old.items():
                    self._view(row, connection=connection)
                    new = replacements.get(task_id)
                    same = new is not None and content_digest(new.model_dump(mode="json")) == row["contract_digest"]
                    if row["state"] in terminal and not same:
                        raise LaneError("PLAN_HISTORY_IMMUTABLE", "Preserve terminal task definitions and add a new task for a changed outcome.")
                    if row["state"] not in terminal and not same:
                        changed.add(task_id)
                if not changed <= set(json.loads(steer["affected_json"])):
                    raise LaneError("STEER_SCOPE_MISMATCH", "The replacement changes tasks outside the recorded affected set.")
                self._validate_modes([task for task in tasks if task.task_id not in old or old[task.task_id]['state'] not in terminal])
                for task_id in replacements.keys() - old.keys():
                    if connection.execute("SELECT 1 FROM plan_tasks WHERE task_id=? LIMIT 1", (task_id,)).fetchone():
                        raise LaneError("PLAN_TASK_ID_REUSED", "Use a fresh identity instead of reviving a historical removed task ID.")
                revision = request.expected_revision + 1
                from .store import require_restoration_plan
                require_restoration_plan(commit.connection('sources'), request.source_restore_digest, revision)
                from .database_recovery import require_recovery_plan
                require_recovery_plan(commit.connection('receipts'), request.database_recovery_digest, revision)
                connection.execute("INSERT INTO plan_revisions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (revision, request.plan_id, request.title, request.request_id, json_text(document), digest,
                     request.expected_revision, actor_id, "semantic_steer:" + request.steer_request_id, now()))
                self._insert_tasks(connection, revision, tasks)
                for task_id, row in old.items():
                    if row["state"] in terminal:
                        connection.execute("UPDATE plan_tasks SET state=?,last_event=? WHERE revision=? AND task_id=?",
                                           (row["state"], row["last_event"], revision, task_id))
                    else:
                        connection.execute("UPDATE plan_tasks SET state='superseded' WHERE revision=? AND task_id=?", (request.expected_revision, task_id))
                        self._event(connection, revision=request.expected_revision, task_id=task_id, kind="task_contract_superseded",
                            actor_id=actor_id, payload={"prior_state": row["state"], "new_revision": revision,
                            "old_contract_digest": row["contract_digest"], "retained_task_id": task_id in replacements})
                if has_jobs:
                    connection.execute("UPDATE jobs_jobs SET state='superseded',resumable=0,updated_at=? "
                        "WHERE plan_revision=? AND state IN ('queued','checkpointed')", (now(), request.expected_revision))
                connection.execute("UPDATE plan_current SET revision=? WHERE singleton=1", (revision,))
                connection.execute("UPDATE steer_requests SET state='superseded',updated_at=? "
                    "WHERE expected_revision=? AND state IN ('pending','checkpointing','ready')", (now(), request.expected_revision))
                connection.execute("UPDATE steer_requests SET state='applied',applied_revision=?,updated_at=? WHERE request_id=?",
                                   (revision, now(), request.steer_request_id))
                if request.resume_after_change and control and control["paused"]:
                    connection.execute("UPDATE steer_control SET paused=0,source_event_id=?,source_cursor=?,actor_id=?,updated_at=? WHERE singleton=1",
                                       (source["event_id"], source["cursor"], actor_id, now()))
                self._event(connection, revision=revision, task_id=None, kind="plan_replaced", actor_id=actor_id,
                    payload={"previous_revision": request.expected_revision, "document_digest": digest,
                             "steer_request_id": request.steer_request_id, "source_cursor": source["cursor"],
                             "changed_task_ids": sorted(changed), "explicit_resume": request.resume_after_change})
                self.store.append_receipt("plan_revision_replaced", {"revision": revision, "previous_revision": request.expected_revision,
                    "document_digest": digest, "steer_request_id": request.steer_request_id,
                    "engine_owned_jobs_quiescent": True, "native_host_tools_attested": False}, connection=connection)
            projection_id = self._prepare_host_projection(connection, revision=revision, actor_id=actor_id)
        self._publish_if_bound(projection_id, lease, actor_id=actor_id)
        return self.snapshot(PlanRead(revision=revision))

    def snapshot(self, request: PlanRead | None = None) -> PlanSnapshot:
        request = request or PlanRead()
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute("BEGIN")
            if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='plan_current' AND type='table'").fetchone():
                return PlanSnapshot(state="no_plan", project_id=self.store.project_id)
            head = connection.execute("SELECT * FROM plan_current WHERE singleton=1").fetchone()
            if head is None:
                return PlanSnapshot(state="no_plan", project_id=self.store.project_id)
            revision = request.revision or head["revision"]
            metadata = connection.execute("SELECT * FROM plan_revisions WHERE revision=?", (revision,)).fetchone()
            if metadata is None:
                raise LaneError("PLAN_REVISION_NOT_FOUND", "This Plan revision does not exist.")
            self._validate_document(metadata)
            counts = {row[0]: row[1] for row in connection.execute(
                "SELECT state,count(*) FROM plan_tasks WHERE revision=? GROUP BY state", (revision,))}
            rows = connection.execute("SELECT * FROM plan_tasks WHERE revision=? ORDER BY position LIMIT ? OFFSET ?",
                                      (revision, request.limit, request.offset)).fetchall()
            tasks = [self._view(row, connection=connection) for row in rows]
            return PlanSnapshot(state="ready", project_id=self.store.project_id, plan_id=metadata["plan_id"],
                                title=metadata["title"], revision=revision, current_revision=head["revision"],
                                document_digest=metadata["document_digest"], total_tasks=sum(counts.values()),
                                offset=request.offset, truncated=request.offset + len(tasks) < sum(counts.values()),
                                counts=counts, tasks=tasks, event_head=head["event_head"])

    def task(self, task_id: str, *, expected_revision: int) -> TaskView:
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute("BEGIN")
            self._head(connection, expected_revision)
            row = self._task(connection, expected_revision, task_id)
            return self._view(row, connection=connection)

    @staticmethod
    def _validate_document(row):
        raw = row['document_json']
        try:
            if len(raw.encode()) > MAX_PLAN_BYTES:
                raise ValueError()
            document = json.loads(raw)
            if (content_digest(document) != row['document_digest'] or any(
                    document.get(key) != row[key] for key in ('plan_id', 'title', 'request_id'))):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise LaneError('PLAN_DOCUMENT_INTEGRITY', 'The stored revision metadata differs from its Plan document.') from None

    @staticmethod
    def _view(row, *, connection=None) -> TaskView:
        definition = TaskDefinition.model_validate_json(row["definition_json"])
        if (definition.task_id != row['task_id']
                or content_digest(definition.model_dump(mode="json")) != row["contract_digest"]):
            raise LaneError("PLAN_CONTRACT_INTEGRITY", "The stored task contract differs from its content digest.")
        if connection is not None:
            dependencies = {item[0] for item in connection.execute(
                'SELECT dependency_id FROM plan_dependencies WHERE revision=? AND task_id=?', (row['revision'], row['task_id']))}
            if dependencies != set(definition.dependencies or []):
                raise LaneError('PLAN_DEPENDENCY_INTEGRITY', 'The dependency index differs from its task contract.')
        return TaskView(position=row["position"], definition=definition, contract_digest=row["contract_digest"],
                        state=row["state"], last_event=row["last_event"])

    def transition(self, task_id: str, state: TaskState, lease: WriterLease, *, expected_revision: int,
                   actor_id: str, event_id: str | None = None, evidence_receipt: str | None = None, transaction=None) -> str:
        """Internal coordinator method; there is no public set-status action."""
        self._lease(lease)
        direct_transaction = transaction is None
        with (lease.transaction('plan', additional_lanes=['sources']) if direct_transaction else nullcontext(transaction)) as connection:
            self.store.require_transaction(connection)
            lease.check(connection)
            self._head(connection, expected_revision)
            row = self._task(connection, expected_revision, task_id)
            self._view(row, connection=connection)
            payload = {"to_state": state, "contract_digest": row["contract_digest"], "evidence_receipt": evidence_receipt}
            if event_id:
                existing = connection.execute("SELECT * FROM plan_events WHERE event_id=?", (event_id,)).fetchone()
                if existing:
                    saved = json.loads(existing["payload_json"])
                    if (existing["task_id"] != task_id or existing["revision"] != expected_revision
                            or existing["actor_id"] != actor_id or any(saved.get(key) != value for key, value in payload.items())):
                        raise LaneError("PLAN_EVENT_CONFLICT", "This event ID belongs to a different task transition.")
                    return existing["digest"]
            from .state_law import LifecycleEvent, transition
            transition(row['state'], LifecycleEvent.TASK_TRANSITION, state, domain='plan_task')
            if state == "active":
                from .store import require_restoration_execution_ready
                with self.project.lane('sources').connection(read_only=True) as sources:
                    require_restoration_execution_ready(sources)
                from .database_recovery import require_recovery_execution_ready
                with self.project.lane('receipts').connection(read_only=True) as receipts:
                    require_recovery_execution_ready(receipts)
                if connection.execute("SELECT 1 FROM plan_tasks WHERE revision=? AND state='active'", (expected_revision,)).fetchone():
                    raise LaneError("PLAN_TASK_ALREADY_ACTIVE", "Finish or checkpoint the active task first.")
                pending = connection.execute("SELECT d.dependency_id FROM plan_dependencies d JOIN plan_tasks t "
                    "ON t.revision=d.revision AND t.task_id=d.dependency_id WHERE d.revision=? AND d.task_id=? AND t.state!='completed'",
                    (expected_revision, task_id)).fetchall()
                if pending:
                    raise LaneError("PLAN_DEPENDENCY_INCOMPLETE", "Complete this task's dependencies first.")
            if state == "completed":
                with self.project.lane('receipts').connection(read_only=True) as receipts:
                    evidence = receipts.execute("SELECT * FROM receipts WHERE receipt_id=?", (evidence_receipt,)).fetchone()
                body = json.loads(evidence["body_json"]) if evidence else {}
                if (not evidence or evidence["kind"] != "delta_exit_verified" or body.get("status") != "passed"
                        or body.get("project_id") != self.store.project_id or body.get("task_id") != task_id
                        or body.get("plan_revision") != expected_revision or body.get("contract_digest") != row["contract_digest"]):
                    raise LaneError("DELTA_VERIFICATION_REQUIRED", "Completion requires the matching verified Delta exit receipt.")
            payload["from_state"] = row["state"]
            connection.execute("UPDATE plan_tasks SET state=? WHERE revision=? AND task_id=?", (state, expected_revision, task_id))
            event_digest = self._event(connection, revision=expected_revision, task_id=task_id, kind="task_transition",
                                       actor_id=actor_id, payload=payload, event_id=event_id)
            projection_id = self._prepare_host_projection(
                connection, revision=expected_revision, actor_id=actor_id)
        if direct_transaction:
            self._publish_if_bound(projection_id, lease, actor_id=actor_id)
        return event_digest

    def verify_history(self, *, limit: int = 50_000) -> dict:
        if not 1 <= limit <= 50_000:
            raise LaneError("INVALID_HISTORY_BUDGET", "Select a bounded Plan history verification limit.")
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute("BEGIN")
            head = self._head(connection)
            rows = connection.execute("SELECT * FROM plan_events ORDER BY sequence LIMIT ?", (limit + 1,)).fetchall()
            if len(rows) > limit:
                raise LaneError("PLAN_HISTORY_BUDGET", "History exceeds this verification budget; use an indexed interval verifier.")
            previous = None
            for sequence, row in enumerate(rows, 1):
                body = {key: row[key] for key in ("sequence", "event_id", "revision", "task_id", "kind", "actor_id", "previous_digest", "created_at")}
                body["payload"] = json.loads(row["payload_json"])
                if row["sequence"] != sequence or row["previous_digest"] != previous or content_digest(body) != row["digest"]:
                    raise LaneError("PLAN_HISTORY_INTEGRITY", "The Plan event history failed its hash-chain check.")
                previous = row["digest"]
            if previous != head["event_head"]:
                raise LaneError("PLAN_HISTORY_INTEGRITY", "The current Plan head differs from its event history.")
            return {"revision": head["revision"], "events_verified": len(rows), "event_head": previous}


def plan_view(store, scope):
    from .lane_contract import ViewGraph
    page = PlanStore(store).snapshot(PlanRead(limit=min(scope.node_limit, 100)))
    graph = ViewGraph(store.project_id, scope)
    ids = {}
    for row in page.tasks:
        ids[row.definition.task_id] = graph.node('plan_task', row.definition.task_id,
            row.definition.title, state=row.state,
            locator={'revision': page.revision, 'contract_digest': row.contract_digest})
    for row in page.tasks:
        for dependency in row.definition.dependencies:
            graph.edge(ids.get(row.definition.task_id), ids.get(dependency), 'DEPENDS_ON')
    graph.truncated |= page.truncated
    return graph.result()


def register_plan_actions(engine):
    def create(context, request):
        store = engine.directory.open(context.project_id, write=True)
        with WriterLease(store, engine.instance_id) as lease:
            return PlanStore(store).create(request, lease, actor_id=context.client_id)

    engine.registry.register(ActionSpec("plan_create", "Create the selected project's initial Plan with bounded task contracts.",
                                       PlanCreate, PlanSnapshot, create, permission="write", profile="plan", mutates=True, workflow='manage-project-plan'))
    engine.registry.register(ActionSpec("plan_read", "Read a bounded page of the authoritative project Plan.",
                                       PlanRead, PlanSnapshot,
                                       lambda context, request: PlanStore(engine.directory.open(context.project_id)).snapshot(request),
                                       profile="plan", queryable_in_delta=True,
                                       cross_project_read=True, read_migrations=PLAN_MIGRATIONS, workflow='manage-project-plan'))
    engine.registry.register(ActionSpec(
        'plan_host_status',
        'Read the full project Plan projection and its exact bound Codex PLAN.md status.',
        HostPlanProjectionRead,
        HostPlanProjection,
        lambda context, request: PlanStore(
            engine.directory.open(context.project_id)
        ).host_projection(request),
        profile='plan', queryable_in_delta=True, studio_read=True,
        read_migrations=PLAN_MIGRATIONS, workflow='manage-project-plan'))

    def bind_host(context, request):
        store = engine.directory.open(context.project_id, write=True)
        with WriterLease(store, engine.instance_id) as lease:
            return PlanStore(store).bind_host_plan(request, lease, actor_id=context.client_id)

    engine.registry.register(ActionSpec(
        'plan_host_bind',
        'Bind and immediately project the full project Plan to one exact local Codex PLAN.md.',
        HostPlanBind, HostPlanBinding, bind_host, permission='admin', profile='plan',
        mutates=True, workflow='manage-project-plan'))

    def sync_host(context, request):
        store = engine.directory.open(context.project_id, write=True)
        with WriterLease(store, engine.instance_id) as lease:
            return PlanStore(store).sync_host_plan(request, lease, actor_id=context.client_id)

    engine.registry.register(ActionSpec(
        'plan_host_sync',
        'Atomically reconcile one pending full project Plan projection with its exact bound PLAN.md.',
        HostPlanProjectionSync, HostPlanProjection, sync_host, permission='write',
        profile='plan', mutates=True, workflow='manage-project-plan'))
    def replace(context, request):
        store = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(store) as lease:
            return PlanStore(store).replace(request, lease, actor_id=context.client_id)

    engine.registry.register(ActionSpec("plan_refresh", "Atomically replace remaining Plan contracts after a captured steer and safe checkpoint.",
                                       PlanReplace, PlanSnapshot, replace, permission="write", profile="plan", mutates=True, workflow='manage-project-plan'))
