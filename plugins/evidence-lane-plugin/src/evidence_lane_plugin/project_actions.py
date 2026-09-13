"""Native project administration through an explicitly granted local owner channel."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, field_validator

from .connections import ProjectSelection
from .errors import LaneError
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .session_authority import SessionAuthority
from .storage import STORAGE_LAYOUT, project_snapshot, reject_links


class ProjectRegister(Contract):
    state_root: str = Field(min_length=1, max_length=1024)
    source_root: str | None = Field(default=None, min_length=1, max_length=1024)
    create: bool = False
    read_only: bool = True
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    sensitivity: Literal['PUBLIC', 'INTERNAL', 'PRIVATE', 'CONFIDENTIAL', 'RESTRICTED'] | None = None
    capture_route: Literal['GOVERNED_PROJECT_FULL', 'ENV_BUILDER_SPARSE'] | None = None

    @field_validator('state_root', 'source_root')
    @classmethod
    def absolute_path(cls, value):
        if value is not None and (not Path(value).is_absolute() or '..' in Path(value).parts
                                  or any(c in value for c in '\r\n\x00')):
            raise ValueError('Select an absolute local folder without parent traversal')
        return value


class ProjectRecord(Contract):
    project_id: str
    state_root: str
    source_root: str
    read_only: bool
    display_name: str
    sensitivity: str | None
    capture_route: str
    registration_digest: str | None
    registration_bound: bool
    sensitivity_enforcement: str
    initial_source_intake: dict[str, JsonValue] | None = None


class ProjectCatalogRequest(Contract):
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=50, ge=1, le=100)


class ProjectCatalog(Contract):
    projects: list[ProjectRecord]
    total: int
    offset: int
    truncated: bool
    selection_changed: bool = False


class ProjectSelected(Contract):
    client_id: str
    project: ProjectRecord
    permissions: list[str]
    native_task_attestation: str = 'not_provided'


class ProjectDeselect(Contract):
    project_id: str = Field(pattern=UUID_PATTERN)
    expected_permissions: list[str] = Field(min_length=1, max_length=7)


class ProjectDeselected(Contract):
    project_id: str
    client_id: str
    deselected: bool = True
    project_data_removed: bool = False


class StorageStatusRequest(Contract):
    pass


class StorageStatus(Contract):
    project_id: str
    source_root: str
    state_root: str
    runtime_root: str
    package_root: str
    root_pv: dict[str, JsonValue]
    lanes: list[dict[str, JsonValue]]
    registration: dict[str, JsonValue]
    storage_selection: dict[str, JsonValue]
    storage_layout: str = STORAGE_LAYOUT
    physical_durability_attested: bool = False
    project_migrated: bool = False


def register_project_actions(engine):
    def register(context, request):
        state_root = Path(request.state_root)
        reject_links(state_root, Path(state_root.anchor))
        resolved = state_root.resolve()
        plugin_root = Path(engine.runtime_identity['package_root']).resolve()
        runtime_root = engine.root.resolve()
        if any(resolved.is_relative_to(root) or root.is_relative_to(resolved) for root in (plugin_root, runtime_root)):
            raise LaneError('PROJECT_RUNTIME_OVERLAP', 'Keep the selected project state separate from plugin and engine installation roots.')
        bootstrap = None
        initial_lane_ids = None
        initializer = None
        if request.create:
            from .project_bootstrap import (
                bootstrap_project,
                classify_initial_source,
                initial_lane_order,
            )
            source_root = Path(request.source_root) if request.source_root else None
            if source_root is None:
                raise LaneError('PROJECT_CREATE_CONTRACT', 'Creating a project requires an explicitly selected source root.')
            classification = classify_initial_source(source_root.resolve(strict=True))
            initial_lane_ids = initial_lane_order(classification)

            def initialize(store):
                nonlocal bootstrap
                bootstrap = bootstrap_project(engine, store, classification, actor_id=context.client_id)

            initializer = initialize
        record = engine.directory.register(state_root,
            source_root=Path(request.source_root) if request.source_root else None,
            create=request.create, read_only=request.read_only, display_name=request.display_name,
            sensitivity=request.sensitivity, capture_route=request.capture_route,
            initial_lane_ids=initial_lane_ids, initializer=initializer)
        return ProjectRecord.model_validate({**record, 'initial_source_intake': bootstrap})

    def read(context, request):
        entries = sorted(engine.directory.entries().values(), key=lambda row: row['project_id'])
        return ProjectCatalog(projects=[ProjectRecord.model_validate(engine.directory.record(row['project_id'])) for row in entries[request.offset:request.offset + request.limit]],
            total=len(entries), offset=request.offset, truncated=request.offset + request.limit < len(entries))

    def select(context, request):
        selected = engine.clients.select_project(context.client_id, request)
        return ProjectSelected(client_id=context.client_id,
            project=ProjectRecord.model_validate(engine.directory.record(request.project_id)),
            permissions=sorted(selected.permissions))

    def deselect(context, request):
        store = engine.directory.open(request.project_id)
        with engine.project_work.control_boundary(store):
            with store.lane('sessions').connection(read_only=True) as connection:
                session = SessionAuthority.current(connection)
            if session and session['state'] == 'active' and session['owner_client_id'] == context.client_id:
                raise LaneError('SESSION_ACTIVE', 'Close this active project session before deselecting it.')
            engine.clients.deselect_project(context.client_id, request.project_id, request.expected_permissions)
            engine.capture.detach_project(context.client_id, request.project_id)
        return ProjectDeselected(project_id=request.project_id, client_id=context.client_id)

    def storage(context, request):
        from .storage_selection import StorageInspect, StorageSelection
        store = engine.directory.open(context.project_id)
        with project_snapshot(store.root):
            return StorageStatus(project_id=store.project_id, source_root=str(store.source_root), state_root=str(store.root),
                runtime_root=str(engine.root), package_root=engine.runtime_identity['package_root'],
                root_pv=store.pv_head(), lanes=store.lane_catalog(), registration=store.registration,
                storage_selection=StorageSelection(engine, store).inspect(context, StorageInspect()).model_dump(mode='json'))

    engine.registry.register(ActionSpec('project_catalog', 'Read registered project roots through the owner-granted native administration channel.',
        ProjectCatalogRequest, ProjectCatalog, read, permission='project_admin', workflow='evidence-lane', project_required=False))
    engine.registry.register(ActionSpec('project_register', 'Register an existing project root or create one direct flat PV root, intake its selected source lane first, then initialize retained authorities without unrelated sectors.',
        ProjectRegister, ProjectRecord, register, permission='project_admin', mutates=True, workflow='select-project-storage', project_required=False))
    engine.registry.register(ActionSpec('project_select', 'Select one registered project and its explicit permissions on this owner-authorized client.',
        ProjectSelection, ProjectSelected, select, permission='project_admin', mutates=True, workflow='open-project-session', project_required=False))
    engine.registry.register(ActionSpec('project_deselect', 'Revoke this client selection at a closed session boundary while preserving the project.',
        ProjectDeselect, ProjectDeselected, deselect, permission='project_admin', mutates=True, workflow='close-project-session', project_required=False))
    engine.registry.register(ActionSpec('storage_status', 'Read distinct source, state, engine and plugin roots and the exact published lane references.',
        StorageStatusRequest, StorageStatus, storage, workflow='select-project-storage', queryable_in_delta=True, studio_read=True))
