"""One typed registry shared by SDK, MCP, skills and event integrations."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError

from .errors import LaneError
from .migrations import Migration

if TYPE_CHECKING:
    from .env_uop_tool_routing import EnvUopRuntime


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    title: str
    description: str
    short_description: str
    default_prompt: str
    skill_name: str | None = None
    source_skill_name: str | None = None

    @property
    def skill(self) -> str:
        if self.skill_name is None:
            raise RuntimeError('Every public workflow requires an explicit business-intent skill name.')
        return self.skill_name

    @property
    def source_skill(self) -> str:
        """Current executable identity; historical names live only in provenance receipts."""
        return self.source_skill_name or self.skill


def _workflow(name, title, description, short, prompt, *, skill, source_skill=None):
    return WorkflowDefinition(name, title, description, short, prompt, skill, source_skill)


# First-class workflows retain their original ownership. A workflow can own
# multiple read and write actions; permissions belong to the exact action.
# Discovery reports empty action sets honestly while a component is unfinished.
WORKFLOWS = (
    _workflow('evidence-lane', 'Evidence Lane',
        'Explore Evidence Lane, inspect the selected project and its evidence, and find the workflow that matches the request. Use for orientation or a read-only project question.',
        'Explore a project and choose the right workflow',
        'Use $evidence-lane to inspect my selected project and choose the right workflow.',
        skill='evidence-lane'),
    _workflow('open-project-session', 'Open project session',
        'Verify the runtime and locked operating-policy package, then start or resume one explicitly selected project session. Use for project entry or client reconnection.',
        'Start or resume a verified project session',
        'Use $open-project-session to start or resume my selected project session.',
        skill='open-project-session'),
    _workflow('execute-project-plan', 'Execute project plan',
        'Execute the next eligible project-plan task through bounded work, selected lane operations and verified completion. Use when carrying out project implementation.',
        'Execute the next eligible project-plan task',
        'Use $execute-project-plan to run the next eligible task in my project plan.',
        skill='execute-project-plan'),
    _workflow('manage-project-plan', 'Manage project plan',
        'Create, inspect or safely steer the current project plan while preserving completed history and replacing only affected work. Use for planning and semantic changes.',
        'Create, inspect or safely steer a project plan',
        'Use $manage-project-plan to inspect or update my project plan.',
        skill='manage-project-plan'),
    _workflow('manage-project-sources', 'Manage project sources',
        'Classify, register, inspect and refresh ordered project sources with exact attribution. Use for files, selected SQLite, source identity, source graphs and authorized Git history.',
        'Register, inspect and refresh project sources',
        'Use $manage-project-sources to register or inspect my selected project sources.',
        skill='manage-project-sources'),
    _workflow('exchange-task-evidence', 'Exchange task evidence',
        'Exchange typed requirements, evidence and results between explicitly selected project tasks while preserving sender and receiver ownership. Use for a governed task exchange.',
        'Exchange attributed evidence between project tasks',
        'Use $exchange-task-evidence to inspect or send the selected task exchange.',
        skill='exchange-task-evidence'),
    _workflow('manage-project-memory', 'Manage project memory',
        'Ingest, query and checkpoint bounded project memory with source attribution. Use for project recall while keeping host recall, instructions, task exchanges and procedural lessons separate.',
        'Retrieve and checkpoint attributed project memory',
        'Use $manage-project-memory to find project evidence relevant to my question.',
        skill='manage-project-memory'),
    _workflow('manage-project-lessons', 'Manage project lessons',
        'Inspect and revoke project-isolated procedural lessons recorded after verified work, or record an explicit host-memory provenance reference without creating a lesson. Use for lesson retrieval, correction or provenance.',
        'Inspect or revoke verified project lessons',
        'Use $manage-project-lessons to inspect lessons relevant to the current task.',
        skill='manage-project-lessons'),
    _workflow('inspect-project-instructions', 'Inspect project instructions',
        'Inspect the selected instruction chain and explicitly selected host recall without merging either into project evidence. Use for instruction provenance and recall boundaries.',
        'Inspect instruction provenance and host recall',
        'Use $inspect-project-instructions to inspect the instructions that apply here.',
        skill='inspect-project-instructions'),
    _workflow('configure-project-workflow', 'Configure project workflow',
        'Derive or change the project workflow from explicitly selected sources and the requested outcome. Use during initial project classification or an explicit workflow change.',
        'Configure project work from sources and outcome',
        'Use $configure-project-workflow to configure work for my sources and requested outcome.',
        skill='configure-project-workflow'),
    _workflow('classify-project-work', 'Classify project work',
        'Classify the requested work, relevant source lanes, action classes and required gates. Use for known work patterns or an explicit custom classification; classification does not authorize execution.',
        'Classify a request and its relevant work lanes',
        'Use $classify-project-work to classify my current request and relevant source lanes.',
        skill='classify-project-work'),
    _workflow('retrieve-project-evidence', 'Retrieve project evidence',
        'Select attributed project evidence within explicit item and estimated-token budgets. Use to fit the relevant evidence into context without merging its owning stores.',
        'Retrieve evidence within item and token budgets',
        'Use $retrieve-project-evidence to select bounded evidence for this task.',
        skill='retrieve-project-evidence'),
    _workflow('refresh-project-evidence', 'Refresh project evidence',
        'Refresh selected sources, lane views and changed authority references after a steer or verified work. Use for explicit evidence refresh and stale view repair.',
        'Refresh changed evidence and lane views',
        'Use $refresh-project-evidence to refresh the selected changed project evidence.',
        skill='refresh-project-evidence'),
    _workflow('inspect-project-connectors', 'Inspect project connectors',
        'Inspect connector grants and their runtime readiness within an Evidence Lane project. Use for connector orientation; configuration and revocation have separate workflows.',
        'Inspect project connector grants and readiness',
        'Use $inspect-project-connectors to inspect the selected project connector grants.',
        skill='inspect-project-connectors'),
    _workflow('configure-project-connector', 'Configure project connector',
        'Configure an Evidence Lane project connector and its bounded grant. Use to add or change connector access within that project.',
        'Configure a bounded project connector grant',
        'Use $configure-project-connector to configure the selected project connector.',
        skill='configure-project-connector'),
    _workflow('revoke-project-connector', 'Revoke project connector',
        'Revoke one Evidence Lane project connector grant while preserving its recorded history. Use for explicit withdrawal of connector access.',
        'Revoke project connector access with history',
        'Use $revoke-project-connector to revoke the selected connector grant.',
        skill='revoke-project-connector'),
    _workflow('select-project-tools', 'Select project tools',
        'Inspect and select lane operation tools, ordered fallbacks and measured compute readiness. Use for toolchain resolution or project accelerator configuration.',
        'Resolve lane tools and measured compute readiness',
        'Use $select-project-tools to inspect the required tools for the selected operation.',
        skill='select-project-tools'),
    _workflow('select-project-storage', 'Select project storage',
        'Inspect or explicitly select durable project state and its separate lane databases. Use for storage routing and project root selection.',
        'Inspect project roots and durable storage routes',
        'Use $select-project-storage to inspect or select my project storage root.',
        skill='select-project-storage'),
    _workflow('inspect-project-evidence-map', 'Inspect project evidence map',
        'Inspect and maintain one project\'s explicitly linked evidence and project references with attributed read-only queries. Use for integrity checks and selected project links.',
        'Inspect one project evidence and link map',
        'Use $inspect-project-evidence-map to inspect my selected project links and evidence map.',
        skill='inspect-project-evidence-map'),
    _workflow('link-project-evidence-network', 'Link project evidence network',
        'Register and link hash-only project summaries in a separate cross-project network without merging project data. Use only for an explicitly selected network.',
        'Link hash-only summaries across projects',
        'Use $link-project-evidence-network to inspect or update the selected cross-project network.',
        skill='link-project-evidence-network'),
    _workflow('handoff-project-work', 'Handoff project work',
        'Transfer current project work between explicitly selected clients with exact ownership checks and attributed continuation context. Use for a requested task handoff.',
        'Transfer exact project work between selected clients',
        'Use $handoff-project-work to inspect or accept the exact offered task continuation.',
        skill='handoff-project-work'),
    _workflow('recover-project-state', 'Recover project state',
        'Back up separate project lanes coherently, restore selected Git history to a fresh folder, or recover after client loss. Use for explicit recovery; accepted-PV rollback is removed.',
        'Back up, verify and recover project state',
        'Use $recover-project-state to inspect or recover my project state.',
        skill='recover-project-state'),
    _workflow('close-project-session', 'Close a project session',
        'Close the selected Evidence Lane project session at a safe work boundary and detach its Flash and capture. Use when explicitly ending that session.',
        'Close a session and detach its capture binding',
        'Use $close-project-session to close my current project session safely.',
        skill='close-project-session'),
    _workflow('run-project-lifecycle', 'Run project lifecycle',
        'Coordinate project entry, planning, bounded implementation, evidence refresh and session closure across the owning first-class workflows. Use for the complete Evidence Lane project lifecycle.',
        'Coordinate the complete project work lifecycle',
        'Use $run-project-lifecycle to continue my project through its current workflow.',
        skill='run-project-lifecycle'),
)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


@dataclass(frozen=True)
class ActionContext:
    """Connection state supplied by the engine, never trusted from tool arguments."""

    client_id: str
    project_id: str | None
    permissions: frozenset[str]
    native_task_id: str | None = None
    request_id: str | None = None
    expected_revision: int | None = None
    allowed_actions: frozenset[str] | None = None
    authorize: Callable[[str], Any] | None = None
    execution: Any = None
    project_context: Callable[[str, str], Any] | None = None
    tool_admission: dict | None = None
    computation: Any = None
    storage_observer: Callable[[str], Any] | None = None
    host_observation: Any = None


@dataclass(frozen=True)
class SearchRoute:
    """An owning read action's exact participation in project lexical search."""
    lanes: tuple[str, ...]
    items: str
    current_action: str | None = None
    current_items: str | None = None
    collection: str | None = None
    match_mode: str | None = None
    basis: str = 'sqlite_fts5_bm25'
    rerank_text: str | None = None

    def schema(self):
        from dataclasses import asdict
        return asdict(self) | {'lanes': list(self.lanes)}


@dataclass(frozen=True)
class FetchRoute:
    """An owning immutable byte reader, with its actual parameter vocabulary."""
    lanes: tuple[str, ...]
    primary_representation: str | None = None
    path_argument: str | None = None
    path_prefix: str = ''
    offset_argument: str = 'offset'
    path_result: str = 'logical_name'
    digest_result: str = 'sha256'
    size_result: str = 'total_bytes'

    def schema(self):
        from dataclasses import asdict
        return asdict(self) | {'lanes': list(self.lanes)}


@dataclass(frozen=True)
class SourceMaterialization:
    """An owner-declared initial index operation; tools retain format selection."""
    path_argument: str = 'filename'
    group_limit: int = 1
    auxiliary_paths: tuple[str, ...] = ()

    def schema(self):
        return {'path_argument': self.path_argument, 'group_limit': self.group_limit,
                'auxiliary_paths': list(self.auxiliary_paths),
                'execution': 'current_plan_delta', 'preparation_is_execution': False}


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    input_model: type[Contract]
    output_model: type[Contract]
    handler: Callable[[ActionContext, Any], Any]
    permission: str = "read"
    profile: str = "core"
    workflow: str = "execute-project-plan"
    mutates: bool = False
    project_required: bool = True
    requires_delta: bool = False
    required_tools: tuple[str, ...] = ()
    worker_operations: tuple[str, ...] = ()
    path_fields: tuple[str, ...] = ()
    queued: bool = False
    queryable_in_delta: bool = False
    studio_read: bool = False
    verification_checks: tuple[str, ...] = ()
    verifier: Callable[[Any, Any, Any], Any] | None = None
    cross_project_read: bool = False
    read_migrations: tuple[Migration, ...] = ()
    tool_routes: tuple[Any, ...] = ()
    ui_resource: str | None = None
    search: SearchRoute | None = None
    fetch: FetchRoute | None = None
    source_lanes: tuple[str, ...] = ()
    materialization: SourceMaterialization | None = None

    def schema(self) -> dict[str, Any]:
        from .tool_routes import operation_contract
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_model.model_json_schema(),
            "outputSchema": self.output_model.model_json_schema(),
            "permission": self.permission,
            "profile": self.profile,
            "workflow": self.workflow,
            "mutates": self.mutates,
            "project_required": self.project_required,
            "requires_delta": self.requires_delta,
            "required_tools": list(self.required_tools),
            "toolchain": operation_contract(self),
            "worker_operations": list(self.worker_operations),
            "path_fields": list(self.path_fields),
            "queued": self.queued,
            "queryable_in_delta": self.queryable_in_delta,
            "studio_read": self.studio_read,
            "verification_checks": list(self.verification_checks),
            "cross_project_read": self.cross_project_read,
            "read_schemas": [{"owner": item.owner, "version": item.version, "digest": item.digest}
                             for item in self.read_migrations],
            **({'project_search': self.search.schema()} if self.search else {}),
            **({'lane_fetch': self.fetch.schema()} if self.fetch else {}),
            **({'source_routes': {'lanes': list(self.source_lanes), 'path_fields': list(self.path_fields),
                'selection': 'delta_enter.source_route', 'implicit_global_default': False}} if self.source_lanes else {}),
            **({'source_materialization': self.materialization.schema()} if self.materialization else {}),
            **({'ui': {'resourceUri': self.ui_resource, 'visibility': ['model']}} if self.ui_resource else {}),
        }


class ActionRegistry:
    def __init__(self) -> None:
        from .tool_routes import ToolRouter
        self.tool_router = ToolRouter()
        self.control_plane: EnvUopRuntime | None = None
        self._actions: dict[str, ActionSpec] = {}
        self._views: dict[str, Any] = {}
        self._selectors: dict[str, Any] = {}
        self._frozen = False

    def register(self, spec: ActionSpec) -> None:
        if self._frozen:
            raise LaneError("REGISTRY_FROZEN", "Actions cannot change after engine startup.")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", spec.name):
            raise LaneError("INVALID_ACTION_NAME", "Action names must be canonical identifiers.")
        if spec.name in self._actions:
            raise LaneError("DUPLICATE_ACTION", "An action is already registered.")
        if not spec.description.strip() or not spec.permission.strip():
            raise LaneError("INVALID_ACTION", "Description and permission are required.")
        if spec.workflow not in {item.name for item in WORKFLOWS}:
            raise LaneError('UNKNOWN_WORKFLOW', 'Actions must belong to a supported user workflow.')
        if spec.mutates and spec.permission == "read":
            raise LaneError("INVALID_ACTION", "A mutation cannot require only read permission.")
        if spec.ui_resource is not None and (
                not re.fullmatch(r'ui://evidence-lane/[a-z0-9-]+\.html', spec.ui_resource)
                or spec.mutates or spec.permission != 'read' or spec.requires_delta or spec.queued):
            raise LaneError('INVALID_ACTION', 'Apps resources belong only to bounded read-only views.')
        if spec.studio_read and (spec.mutates or spec.permission != 'read' or spec.requires_delta or spec.queued or not spec.project_required):
            raise LaneError('INVALID_ACTION', 'Studio reads require a project read operation without execution or queueing.')
        if spec.requires_delta and (not spec.project_required or spec.queued):
            raise LaneError("INVALID_ACTION", "Delta operations require a project and cannot enqueue nested work.")
        if spec.source_lanes:
            from .lanes import LANE_REGISTRY
            if (not spec.requires_delta or not spec.path_fields or 'lane_id' not in spec.input_model.model_fields
                    or len(set(spec.source_lanes)) != len(spec.source_lanes)
                    or not set(spec.path_fields) <= set(spec.input_model.model_fields)
                    or any(lane not in LANE_REGISTRY or LANE_REGISTRY[lane].kind != 'sector' for lane in spec.source_lanes)):
                raise LaneError('INVALID_SOURCE_ROUTE', 'Source consumers require explicit sector lanes and typed Delta source paths.')
        if spec.materialization and (not spec.source_lanes or not spec.verifier
                or spec.materialization.path_argument not in spec.path_fields
                or not 1 <= spec.materialization.group_limit <= 32
                or len(set(spec.materialization.auxiliary_paths)) != len(spec.materialization.auxiliary_paths)
                or len(spec.materialization.auxiliary_paths) > 8
                or any(not isinstance(path, str) or not path or len(path) > 1000
                       for path in spec.materialization.auxiliary_paths)):
            raise LaneError('INVALID_MATERIALIZATION_ROUTE', 'Preparation requires an owning verified source consumer and bounded path groups.')
        if spec.queryable_in_delta and (spec.mutates or spec.permission != "read" or spec.requires_delta or spec.queued):
            raise LaneError("INVALID_ACTION", "Delta queries must be bounded read-only actions.")
        if spec.cross_project_read and (not spec.queryable_in_delta or not spec.project_required):
            raise LaneError("INVALID_ACTION", "Cross-project queries require an admitted project read action.")
        if spec.search is not None:
            from .lanes import LANE_REGISTRY
            route = spec.search
            if (not spec.queryable_in_delta or not spec.project_required or not route.lanes
                    or len(set(route.lanes)) != len(route.lanes)
                    or not set(route.lanes) <= set(LANE_REGISTRY)
                    or bool(route.current_action) != bool(route.current_items)
                    or (route.rerank_text is not None and (route.basis != 'sqlite_fts5_bm25'
                        or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', route.rerank_text)))
                    or 'query' not in spec.input_model.model_fields
                    or 'limit' not in spec.input_model.model_fields):
                raise LaneError('INVALID_SEARCH_ROUTE', 'Search requires canonical lanes and an owning bounded read action.')
            for existing in self._actions.values():
                if existing.search and set(existing.search.lanes) & set(route.lanes):
                    raise LaneError('DUPLICATE_SEARCH_ROUTE', 'Each lane has one canonical project search reader.')
        if spec.fetch is not None:
            from .lanes import LANE_REGISTRY
            fetch_route = spec.fetch
            if (not spec.queryable_in_delta or not spec.project_required or not fetch_route.lanes
                    or len(set(fetch_route.lanes)) != len(fetch_route.lanes)
                    or not set(fetch_route.lanes) <= set(LANE_REGISTRY)
                    or not {'snapshot_id', 'max_bytes', fetch_route.offset_argument} <= set(spec.input_model.model_fields)):
                raise LaneError('INVALID_FETCH_ROUTE', 'Fetch requires canonical lanes and a bounded immutable snapshot reader.')
            for existing in self._actions.values():
                if existing.fetch and set(existing.fetch.lanes) & set(fetch_route.lanes):
                    raise LaneError('DUPLICATE_FETCH_ROUTE', 'Each lane has one canonical immutable byte reader.')
        if spec.verifier is not None and (not spec.requires_delta or not spec.verification_checks
                                         or len(set(spec.verification_checks)) != len(spec.verification_checks)):
            raise LaneError("INVALID_ACTION", "A verifier requires distinct named Delta acceptance checks.")
        if not issubclass(spec.input_model, Contract) or not issubclass(
            spec.output_model, Contract
        ):
            raise LaneError("INVALID_CONTRACT", "Actions require strict typed contracts.")
        from .tool_routes import validate_routes
        validate_routes(spec)
        self._actions[spec.name] = spec

    def freeze(self) -> None:
        for spec in self._actions.values():
            if spec.fetch:
                route = spec.fetch
                try:
                    for lane_id in route.lanes:
                        for representation in ([route.primary_representation, 'original_source'] if route.primary_representation else [None]):
                            payload = {'snapshot_id': '0' * 64, route.offset_argument: 0, 'max_bytes': 1024}
                            if 'lane_id' in spec.input_model.model_fields:
                                payload['lane_id'] = lane_id
                            if representation:
                                payload['representation'] = representation
                            if route.path_argument:
                                payload[route.path_argument] = route.path_prefix + 'route_validation.txt'
                            spec.input_model.model_validate(payload)
                except ValidationError:
                    raise LaneError('INVALID_FETCH_ROUTE', 'The fetch route does not match its owning typed reader.') from None
            if spec.search:
                search_route = spec.search
                current = self.get(search_route.current_action) if search_route.current_action else None
                if current and (not current.queryable_in_delta or not current.project_required):
                    raise LaneError('INVALID_SEARCH_ROUTE', 'Snapshot selection requires an admitted project reader.')
                try:
                    for lane_id in search_route.lanes:
                        payload = {'query': 'route_validation', 'limit': 1}
                        if 'lane_id' in spec.input_model.model_fields:
                            payload['lane_id'] = lane_id
                        if current:
                            current.input_model.model_validate({'lane_id': lane_id} if 'lane_id' in current.input_model.model_fields else {})
                            payload['snapshot_id'] = '0' * 64
                        if search_route.collection:
                            payload['collection'] = search_route.collection
                        if search_route.match_mode:
                            payload['match_mode'] = search_route.match_mode
                        spec.input_model.model_validate(payload)
                except ValidationError:
                    raise LaneError('INVALID_SEARCH_ROUTE', 'The search route does not match its owning typed reader.') from None
        self._frozen = True

    def register_selector(self, owner):
        if self._frozen:
            raise LaneError('REGISTRY_FROZEN', 'Selector ownership cannot change after engine startup.')
        owner.validate()
        if owner.lane_id in self._selectors:
            raise LaneError('DUPLICATE_SELECTOR_OWNER', 'Each sector has one owning local-source selector adapter.')
        self._selectors[owner.lane_id] = owner

    def selector_owner(self, lane_id):
        from dataclasses import replace

        from .lanes import is_named_custom_lane
        if is_named_custom_lane(lane_id) and 'custom' in self._selectors:
            from .sector_evidence_profile import read_snapshot
            return replace(self._selectors['custom'], lane_id=lane_id,
                read_manifest=lambda store, snapshot_id: read_snapshot(store, lane_id, snapshot_id)[0])
        try:
            return self._selectors[lane_id]
        except KeyError:
            raise LaneError('SOURCE_SELECTOR_OWNER_UNAVAILABLE', 'Select a registered owning local-source sector.') from None

    def selector_schemas(self):
        return [self._selectors[key].schema() for key in sorted(self._selectors)]

    def register_view(self, spec) -> None:
        if self._frozen:
            raise LaneError("REGISTRY_FROZEN", "View contracts cannot change after engine startup.")
        spec.validate()
        if spec.view_id in self._views:
            raise LaneError("DUPLICATE_VIEW", "A lane view contract is already registered.")
        for registered in self._views.values():
            if registered.folder == spec.folder and registered.lane_id != spec.lane_id:
                raise LaneError("VIEW_FOLDER_OWNER_CONFLICT", "A lane artifact folder has exactly one owner.")
        self._views[spec.view_id] = spec

    def get_view(self, view_id):
        from .lanes import is_named_custom_lane
        lane_id, separator, name = view_id.partition('.')
        if separator and name == 'structure' and is_named_custom_lane(lane_id) and 'custom.structure' in self._views:
            from .sector_evidence_views import named_custom_view
            return named_custom_view(self._views['custom.structure'], lane_id)
        if view_id not in self._views:
            raise LaneError("UNKNOWN_LANE_VIEW", "Select an implemented lane view contract.")
        return self._views[view_id]

    def view_schemas(self) -> list[dict]:
        return [self._views[key].schema() for key in sorted(self._views)]

    @property
    def frozen(self) -> bool:
        return self._frozen

    def get(self, name: str) -> ActionSpec:
        try:
            return self._actions[name]
        except KeyError:
            raise LaneError("UNKNOWN_ACTION", "The action is not registered.") from None

    def schemas(self) -> list[dict[str, Any]]:
        return [self._actions[name].schema() for name in sorted(self._actions)]

    def search_actions(self) -> list[ActionSpec]:
        return [self._actions[name] for name in sorted(self._actions) if self._actions[name].search]

    def fetch_actions(self) -> list[ActionSpec]:
        return [self._actions[name] for name in sorted(self._actions) if self._actions[name].fetch]

    def materialization_actions(self) -> list[ActionSpec]:
        return [self._actions[name] for name in sorted(self._actions) if self._actions[name].materialization]

    def workflow_schemas(self, selected: str | None = None) -> list[dict[str, Any]]:
        if selected is not None and selected not in {item.name for item in WORKFLOWS}:
            raise LaneError('UNKNOWN_WORKFLOW', 'Select a registered public workflow.')
        actions = self.schemas()
        return [{
            'name': item.name, 'skill': item.skill, 'title': item.title,
            'description': item.description,
            'availability': 'registered_actions' if any(action['workflow'] == item.name for action in actions) else 'no_registered_actions',
            'actions': [{key: action[key] for key in (
                'name', 'description', 'profile', 'permission', 'mutates', 'project_required',
                'requires_delta', 'queued', 'queryable_in_delta', 'studio_read', 'cross_project_read')}
                for action in actions if action['workflow'] == item.name],
        } for item in WORKFLOWS if selected is None or item.name == selected]

    def validate(self, name: str, payload: dict[str, Any], context: ActionContext) -> Contract:
        spec = self.get(name)
        if context.allowed_actions is not None and name not in context.allowed_actions:
            raise LaneError("ACTION_SCOPE_DENIED", "The connection does not grant this action.")
        if context.authorize is not None:
            context.authorize(spec.permission)
        if spec.permission not in context.permissions:
            raise LaneError("PERMISSION_DENIED", "This connection lacks the action permission.")
        if spec.project_required and context.project_id is None:
            raise LaneError("PROJECT_REQUIRED", "Select a project for this action.")
        try:
            return spec.input_model.model_validate(payload)
        except ValidationError as error:
            # Pydantic errors contain raw input unless explicitly excluded.
            raise LaneError(
                "INVALID_ARGUMENTS",
                "The action arguments do not match its contract.",
                details={
                    "errors": [{**item, "loc": list(item["loc"])} for item in error.errors(
                        include_input=False, include_context=False, include_url=False
                    )]
                },
            ) from None

    def execute(self, name: str, payload: dict[str, Any], context: ActionContext) -> dict[str, Any]:
        result, _ = self.execute_attributed(name, payload, context)
        return result

    def execute_attributed(self, name: str, payload: dict[str, Any], context: ActionContext):
        arguments = self.validate(name, payload, context)
        spec = self.get(name)
        if spec.requires_delta and context.execution is None:
            raise LaneError("DELTA_REQUIRED", "Enter the exact Plan task to execute this operation.")
        return self.tool_router.invoke(spec, context, arguments, control_plane=self.control_plane)
