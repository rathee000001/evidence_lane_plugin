"""Executable bindings for the eight separate authority packages.

The old support module generated a second index and a mandatory artifact bundle
for each authority. The current binding uses each real owner implementation,
its own migration history and files, and the published project evidence head coordinator references.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from types import MappingProxyType

from .errors import LaneError
from .lanes import get_lane, lane_artifact_contract, lane_schema_asset
from .migrations import apply_migrations, read_compatibility
from .storage import LaneStore, ProjectStore, project_snapshot


@dataclass(frozen=True)
class AuthoritySupportProfile:
    authority_id: str
    runtime_module: str
    migration_bindings: tuple[tuple[str, str], ...]
    foundation_owners: tuple[str, ...] = ()

    @property
    def database(self):
        return get_lane(self.authority_id).database_relative_path

    @property
    def package_folder(self):
        return 'authorities/' + AUTHORITY_SOURCE_OWNERS[self.authority_id]


AUTHORITY_SOURCE_OWNERS = MappingProxyType({
    'plan': 'plan', 'chat_lineage': 'chat_lineage', 'canon': 'canon_input',
    'memory': 'project_memory', 'learning': 'agent_learning', 'sources': 'source_authority',
    'receipts': 'receipt_ledger', 'universe': 'project_universe',
})
PROJECT_COORDINATION_FOLDER = 'authorities/project_authority'

# Workflow ownership may span several storage owners. These are source package
# bindings only; they neither alter grants nor reroute an operation's database.
_ACTION_PROFILES = MappingProxyType({
    'plan': ('plan', 'delta'), 'chat_lineage': ('chatlineage', 'chat_lineage', 'continuity'),
    'canon': ('canon',), 'memory': ('memory',), 'learning': ('learning',),
    'sources': ('sources',), 'receipts': ('receipts', 'extensions', 'runtime', 'sessions'),
    'universe': ('universe',), 'project_authority': ('projects', 'recovery'),
    'session_authority': ('sessions',), 'instructions': (),
})
_ACTION_NAMES = MappingProxyType({
    'sources': ('git_branch_authority', 'enroll_project', 'fetch', 'git_sync_selected'),
    'receipts': ('capture_bind', 'remote_git_action_read', 'remote_git_prepare_push', 'remote_git_execute_push'),
    'project_authority': ('project_catalog', 'project_deselect', 'project_register', 'project_select',
                          'project_status', 'storage_status', 'code_snapshot_summary'),
    'session_authority': ('session_flash_status', 'session_exit_boundary', 'env_uop_inspect'),
    'instructions': ('instructions_inspect',),
})


def authority_package_folder(authority_id):
    """Original source owner, distinct from authorities/<lane> project state."""
    return authority_profile(authority_id).package_folder


def authority_actions(registry, owner_id):
    if owner_id not in _ACTION_PROFILES:
        raise LaneError('AUTHORITY_OWNER_UNKNOWN', 'Select a retained authority or workflow owner.')
    named = set(_ACTION_NAMES.get(owner_id, ()))
    # Resolve named exceptions now, so a removed public route cannot silently
    # disappear from the owning package or leave a stale wrapper behind.
    for name in named:
        registry.get(name)
    return [row for row in registry.schemas()
            if row['profile'] in _ACTION_PROFILES[owner_id] or row['name'] in named]


def call_authority_action(client, allowed_actions, *, action, project_id=None,
                          arguments=None, expected_revision=None, request_id=None):
    """Original reader/builder interface, using the authenticated public SDK."""
    if action not in allowed_actions:
        raise LaneError('AUTHORITY_ACTION_MISMATCH', 'Use this owner entrypoint for one of its declared actions.')
    if allowed_actions[action] and project_id is None:
        raise LaneError('PROJECT_REQUIRED', 'Select the exact project for this authority operation.')
    return client.call(action, project_id=project_id, arguments=arguments,
                       expected_revision=expected_revision, request_id=request_id)


AUTHORITY_SUPPORT_PROFILES = MappingProxyType({
    'plan': AuthoritySupportProfile('plan', 'plan_runtime', (
        ('plan_runtime', 'PLAN_MIGRATIONS'), ('steering', 'STEER_MIGRATIONS'),
        ('acceptance', 'VALIDATION_MIGRATIONS'),
        ('acceptance', 'VALIDATION_RUN_MIGRATIONS'),
        ('jobs', 'JOB_MIGRATIONS'), ('adaptive_delta_entry', 'DELTA_MIGRATIONS'))),
    'chat_lineage': AuthoritySupportProfile('chat_lineage', 'lineage', (
        ('lineage', 'LINEAGE_MIGRATIONS'), ('task_binding_registry', 'CONTINUATION_MIGRATIONS'),
        ('prompt_index', 'PROMPT_MIGRATIONS'), ('codex_turn_control', 'TURN_MIGRATIONS'))),
    'canon': AuthoritySupportProfile('canon', 'canon_task_graph', (('canon_task_graph', 'CANON_MIGRATIONS'),)),
    'memory': AuthoritySupportProfile('memory', 'project_memory', (('project_memory', 'MEMORY_MIGRATIONS'),)),
    'learning': AuthoritySupportProfile('learning', 'agent_learning', (('agent_learning', 'LEARNING_MIGRATIONS'),)),
    'sources': AuthoritySupportProfile('sources', 'source_authority', (
        ('source_authority', 'SOURCES_MIGRATIONS'), ('store', 'RESTORE_MIGRATIONS'),
        ('enrollment', 'GIT_BRANCH_MIGRATIONS'), ('source_routing', 'ROUTE_MIGRATIONS'),
        ('source_materialization', 'MATERIALIZATION_MIGRATIONS'), ('custom_lanes', 'MIGRATIONS'))),
    'receipts': AuthoritySupportProfile('receipts', 'projects', (
        ('projects', 'ACCESS_MIGRATIONS'), ('connector_governance', 'EXTENSION_MIGRATIONS'),
        ('accelerators', 'ACCELERATOR_MIGRATIONS'), ('remote_api', 'REMOTE_MIGRATIONS'),
        ('database_recovery', 'RECOVERY_MIGRATIONS'), ('session_authority', 'SESSION_MIGRATIONS'),
        ('remote_git', 'PUSH_MIGRATIONS'), ('capture_routing', 'CAPTURE_MIGRATIONS'),
        ('agent_learning', 'HOST_MEMORY_MIGRATIONS'), ('storage_selection', 'STORAGE_MIGRATIONS')),
        foundation_owners=('receipts',)),
    'universe': AuthoritySupportProfile('universe', 'project_universe', (
        ('project_universe', 'UNIVERSE_MIGRATIONS'), ('universe_federation', 'FEDERATION_MIGRATIONS'))),
})


def authority_profile(authority_id):
    definition = get_lane(authority_id)
    if definition.kind != 'authority':
        raise LaneError('AUTHORITY_REQUIRED', 'Select one of the eight authority lanes.')
    return AUTHORITY_SUPPORT_PROFILES[definition.canonical_lane_id]


def authority_migrations(authority_id):
    profile = authority_profile(authority_id)
    migrations = tuple(migration for module, name in profile.migration_bindings
                       for migration in getattr(import_module('.' + module, __package__), name))
    declared = set(get_lane(profile.authority_id).schema_owners)
    owners = {migration.owner for migration in migrations}
    foundation = set(profile.foundation_owners)
    expected_foundation = {'receipts'} if profile.authority_id == 'receipts' else set()
    if foundation != expected_foundation or owners & foundation or owners | foundation != declared:
        raise LaneError('AUTHORITY_SCHEMA_BINDINGS', 'Bind every declared schema owner to its owning implementation.',
                        details={'authority_id': profile.authority_id,
                                 'missing_owners': sorted(declared - owners - foundation),
                                 'unexpected_owners': sorted((owners | foundation) - declared)})
    for owner in owners:
        versions = sorted(migration.version for migration in migrations if migration.owner == owner)
        if versions != list(range(1, len(versions) + 1)):
            raise LaneError('AUTHORITY_MIGRATION_SEQUENCE', 'Bind each owner migration exactly once from version one.',
                            details={'authority_id': profile.authority_id, 'owner': owner})
    return migrations


def _store(value, authority_id, *, write=False):
    profile = authority_profile(authority_id)
    if isinstance(value, LaneStore):
        if value.lane_id != profile.authority_id:
            raise LaneError('AUTHORITY_STORE_MISMATCH', 'The selected store belongs to another authority.')
        store = value
    else:
        project = value if isinstance(value, ProjectStore) else ProjectStore(Path(value), read_only=not write)
        store = project.lane(profile.authority_id)
    if write and store.read_only:
        raise LaneError('READ_ONLY_PROJECT', 'Authority initialization requires project write ownership.')
    return store


def canonical_authority_module(authority_id):
    return import_module('.' + authority_profile(authority_id).runtime_module, __package__)


def refresh_authority_support(project_root, authority_id, *, writer=None):
    """Initialize the real owning schemas; create no duplicate authority index."""
    # Validate coverage before opening the selected authority for initialization.
    migrations = authority_migrations(authority_id)
    store = _store(project_root, authority_id, write=True)
    applied = apply_migrations(store, migrations, writer=writer)
    return {'authority_id': store.lane_id, 'project_id': store.project_id,
            'migrations_applied': applied, 'database': str(store.database),
            'files_root': str(store.files), 'root_pv': store.project.pv_head(),
            'automatic_graph_exports': False, 'separate_tool_installation': False}


def validate_authority_support(project_root, authority_id, *, max_schema_objects=512):
    """Read actual owner history and the matching published lane head."""
    if not 1 <= max_schema_objects <= 4096:
        raise LaneError('AUTHORITY_READ_BUDGET', 'Select a bounded schema inspection limit.')
    store = _store(project_root, authority_id)
    with project_snapshot(store.root):
        compatibility = read_compatibility(store, authority_migrations(authority_id))
        with store.connection(read_only=True) as connection:
            objects = [dict(row) for row in connection.execute(
                "SELECT name,type,tbl_name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name LIMIT ?",
                (max_schema_objects + 1,))]
            if len(objects) > max_schema_objects:
                raise LaneError('AUTHORITY_READ_BUDGET', 'The schema exceeds the selected inspection limit.')
            file_count = connection.execute('SELECT COUNT(*) FROM objects').fetchone()[0]
        head = next(row for row in store.project.lane_catalog() if row['lane_id'] == store.lane_id)
        return {'authority_id': store.lane_id, 'project_id': store.project_id,
                'schema_compatibility': compatibility, 'schema_objects': objects,
                'registered_file_count': file_count, 'lane_head': head,
                'root_pv': store.project.pv_head(), 'mutation_performed': False}


def materialize_missing_authority_tools_contract(project_root, authority_id):
    """Return the current shared-tool reference without making a per-lane bundle."""
    store = _store(project_root, authority_id)
    return {'authority_id': store.lane_id, 'project_id': store.project_id,
            'artifact_contract': lane_artifact_contract(store.lane_id),
            'toolchain_selection': 'owning operation contract and measured engine readiness',
            'tool_dependencies_installed': False, 'files_created': False}


def refresh_delta_exit_authority_supports(project_root, *, affected_authorities):
    """Return references to explicitly affected authorities after verified work."""
    project = project_root if isinstance(project_root, ProjectStore) else ProjectStore(Path(project_root), read_only=True)
    selected = tuple(dict.fromkeys(authority_profile(name).authority_id for name in affected_authorities))
    if not selected:
        raise LaneError('AFFECTED_AUTHORITIES_REQUIRED', 'Select the authorities changed by this verified exit.')
    with project_snapshot(project.root):
        return {'project_id': project.project_id, 'root_pv': project.pv_head(),
                'authorities': [validate_authority_support(project, name) for name in selected],
                'mutation_performed': False, 'automatic_artifact_bundle': False}


def validate_delta_exit_authority_supports(project_root, *, affected_authorities):
    return refresh_delta_exit_authority_supports(project_root, affected_authorities=affected_authorities)


def authority_package_contract(authority_id, registry=None):
    profile = authority_profile(authority_id)
    contract = {'schema': 'evidence-lane.authority-package.v4', 'authority_id': profile.authority_id,
            'source_package_folder': profile.package_folder,
            'project_state_folder': get_lane(profile.authority_id).folder,
            'runtime_module': 'evidence_lane_plugin.' + profile.runtime_module,
            'schema_contract': lane_schema_asset(profile.authority_id),
            'artifacts': lane_artifact_contract(profile.authority_id),
            'migration_bindings': [{'module': module, 'constant': name} for module, name in profile.migration_bindings],
            'foundation_schema_owners': list(profile.foundation_owners),
            'foundation_schema_source': 'evidence_lane_plugin.storage.RECEIPTS_SCHEMA' if profile.foundation_owners else None,
            'migration_history': [{'owner': item.owner, 'version': item.version, 'digest': item.digest}
                                  for item in authority_migrations(profile.authority_id)],
            'runtime_database_packaged': False, 'installed_execution_claimed': False}
    if registry is not None:
        contract['actions'] = authority_actions(registry, authority_id)
        contract['views'] = [row for row in registry.view_schemas() if row['lane_id'] == profile.authority_id]
    return contract
