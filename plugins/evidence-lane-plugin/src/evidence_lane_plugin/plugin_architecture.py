"""Inspectable architecture derived from executable actions and owning lane contracts.

This is a source projection. It does not attest an installed host, materialize a
project or infer tool readiness from an inventory entry. Graphs are generated
only by an explicit consumer through the retained semantic graph renderer.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .authority_support import (
    PROJECT_COORDINATION_FOLDER,
    authority_actions,
    authority_package_contract,
    authority_package_folder,
)
from .errors import LaneError
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .hook_contract import hook_registry
from .lanes import (
    AUTHORITY_LANE_IDS,
    CANONICAL_LANE_IDS,
    get_lane,
    lane_artifact_contract,
    lane_schema_asset,
)
from .package_root import resolve_plugin_root

PLUGIN_ARCHITECTURE_SCHEMA = 'evidence-lane.universal-plugin-architecture.v4'
TOOL_ROLE_CLASSES = ('TASK_EXECUTION', 'TRANSPORT_OR_ORCHESTRATION',
                    'OBSERVABILITY_OR_EVALUATION_ATTACHMENT', 'EXTERNAL_SERVICE_OR_STORE')
OPERATING_CYCLE_STAGES = (
    {'id': 'INTENT', 'label': 'User intent and selected native skill'},
    {'id': 'CONTEXT', 'label': 'Bound project and measured host capabilities'},
    {'id': 'CONTRACT', 'label': 'Current action and lane contracts'},
    {'id': 'PLAN', 'label': 'Current Plan revision and one active Delta'},
    {'id': 'TOOLS', 'label': 'Eligible primary and fallback tools'},
    {'id': 'EXECUTE', 'label': 'Bounded execution with project writer fencing'},
    {'id': 'VERIFY', 'label': 'Verify outputs and reject stale completion'},
    {'id': 'PUBLISH', 'label': 'Coordinated lane heads and project evidence head coordinator publication'},
    {'id': 'LEARNING', 'label': 'Evidence and Learning after verified Delta exit'},
    {'id': 'NEXT', 'label': 'Read-only Studio updates and next eligible task'},
)
UNIVERSAL_ROUTING_STAGES = (
    {'stage': 'intent_and_skill_resolution', 'source': 'registry.WORKFLOWS'},
    {'stage': 'typed_action_resolution', 'source': 'registry.ActionRegistry'},
    {'stage': 'native_transport', 'source': 'sdk.EvidenceLaneClient and mcp_adapter'},
    {'stage': 'persistent_engine_api', 'source': 'local_transport.LocalEndpoint'},
    {'stage': 'project_and_lane_admission', 'source': 'projects.ProjectAccess and adaptive_delta_entry'},
    {'stage': 'operation_execution', 'source': 'owning_action_handler and workers.WorkerPool'},
    {'stage': 'coordinated_publication', 'source': 'storage.ProjectStore and writers.WriterLease'},
    {'stage': 'human_observation', 'source': 'studio_gateway.StudioGateway.snapshot'},
)
MEMORY_AUTHORITY_MAP = (
    {'memory_class': 'sensory_live_capture', 'owners': ['capture_routing'], 'durable_authority': False,
     'role': 'Visible host events require explicit project/session binding before persistence.'},
    {'memory_class': 'task_working_context', 'owners': ['connections', 'plan', 'chat_lineage'], 'durable_authority': False,
     'role': 'Connection identity and bounded task context do not establish native host attestation.'},
    {'memory_class': 'episodic_chat_lineage', 'owners': ['chat_lineage'], 'durable_authority': True,
     'role': 'Attributed append-only events, turns and task-continuation history.'},
    {'memory_class': 'project_memory', 'owners': ['memory'], 'durable_authority': True,
     'role': 'Bounded locators and evidence links; original authorities remain separate.'},
    {'memory_class': 'agent_learning', 'owners': ['learning'], 'durable_authority': True,
     'role': 'Versioned evidence-backed lessons appended after verified Delta exit.'},
    {'memory_class': 'canon_exchanges', 'owners': ['canon'], 'durable_authority': True,
     'role': 'Typed attributed exchanges preserve receiver ownership and decisions.'},
    {'memory_class': 'host_instructions_and_recall', 'owners': ['AGENTS.md', 'host_MEMORY.md'], 'durable_authority': False,
     'role': 'Host-owned instructions and recall never become a project authority.'},
)


def build_engine_connection_contract(plugin_root, *, registry):
    """Replace the old hidden-tunnel assertion with inspectable connection owners."""
    root = Path(plugin_root)
    owners = ['.mcp.json', 'scripts/run_mcp.py', 'scripts/run_engine.py',
        'src/evidence_lane_plugin/launcher.py', 'src/evidence_lane_plugin/service.py',
        'src/evidence_lane_plugin/connections.py', 'src/evidence_lane_plugin/local_transport.py',
        'src/evidence_lane_plugin/projects.py',
        'src/evidence_lane_plugin/remote_api.py', 'src/evidence_lane_plugin/remote_transport.py',
        'src/evidence_lane_plugin/mcp_server.py', 'src/evidence_lane_plugin/mcp_adapter.py',
        'src/evidence_lane_plugin/capture_routing.py', 'src/evidence_lane_plugin/hook_contract.py',
        'src/evidence_lane_plugin/session.py', 'src/evidence_lane_plugin/session_authority.py',
        'src/evidence_lane_plugin/flash_authority.py', 'src/evidence_lane_plugin/host_routing.py',
        'src/evidence_lane_plugin/endpoint_credentials.py',
        'authorities/session_authority/installation-layout.v4.json']
    return {'schema': 'evidence-lane.engine-connection-contract.v4', 'status': 'source_contract',
        'superseded_hidden_bearer_route_retired': True,
        'entrypoint': 'scripts/run_mcp.py', 'stdio_contract': _read_json(root / '.mcp.json'),
        'bootstrap_dependency': 'Python 3.14 command and pinned core environment; complete first-detection installer is a separate qualification',
        'installed_plugin_location': 'Codex-managed plugin root',
        'local_api': {'owner': 'local_transport.LocalEndpoint', 'origin': 'current-user loopback only',
            'connection_route': '/v4/connect', 'action_route': '/v4/action', 'capture_route': '/v4/capture',
            'project_admin': 'separate local-owner bootstrap grant',
            'windows_credentials': 'current-user DPAPI', 'posix_credentials': 'owned private file with no-follow checks'},
        'remote_api': {'owner': 'remote_api.RemoteGateway', 'origin': 'explicit HTTPS with verified TLS',
            'routes': ['discover', 'probe', 'verify', 'connect', 'action', 'capture', 'disconnect'],
            'route_prefix': '/remote/v4/', 'authorization': 'exact parent grant plus a unique engine-issued child connection',
            'durability': 'server-enforced recent SQLite/object read after engine restart and operator persistent declaration',
            'read_only_setup': 'server owner may prepare the exact grant-bound restart probe; the client can verify it without probe-write permission',
            'physical_volume_durability': 'operator_declaration_only', 'project_admin': False,
            'hook_provenance': 'authenticated_remote_hook_report', 'automatic_mutation_retry': False},
        'platforms': {'Windows': {'studio': True, 'runtime': 'C:/Apps/EvidenceLaneStudio/engine'},
            'Darwin': {'studio': False, 'runtime': '~/Library/Application Support/EvidenceLane/runtime'},
            'Linux': {'studio': False, 'runtime': '~/.local/share/EvidenceLane/runtime'},
            'codex_vm_persistent': {'requires_verified_remote_route': True},
            'codex_vm_ephemeral': {'requires_verified_remote_route': True}},
        'studio': {'owner_source': 'apps/evidence-lane-studio', 'human_access': 'visible_read_only',
            'windows_pc_only': True,
            'shared_installation': 'authorities/session_authority/installation-layout.v4.json'},
        'project_state': 'direct flat-PV lane SQLite databases, objects and projections coordinated by the root-PV head',
        'session_state_owner': 'sessions lane', 'workflow_count': len(registry.workflow_schemas()),
        'action_count': len(registry.schemas()),
        'source_members': [{'path': path, 'sha256': sha256_file(root / path)} for path in owners],
        'installed': False, 'native_task_attestation': 'not_provided', 'hook_trust_attested': False,
        'full_toolchain_readiness': 'not_established_by_this_contract',
        'retained_provisioning_source': 'scripts/first_detection.py',
        'legacy_installer_called_by_current_entrypoint': False}


def _safe_graph_id(prefix, value):
    return prefix + '_' + ''.join(c if c.isalnum() else '_' for c in value)


def _read_json(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise LaneError('PACKAGE_CONTRACT_BUDGET', 'The selected package contract exceeds its budget.')
    return json.loads(raw)


def _validated_owner_package(root, folder):
    manifest = _read_json(root / folder / 'manifest.v4.json')
    members = manifest.get('members', [])
    if not members or len({row['path'] for row in members}) != len(members):
        raise LaneError('AUTHORITY_PACKAGE_MEMBERS_INVALID', 'Require unique hashed members for this owning package.')
    for row in members:
        path = root / row['path']
        if (not path.resolve().is_relative_to(root / folder) or not path.is_file()
                or sha256_file(path).lower() != row['sha256'].lower()):
            raise LaneError('AUTHORITY_PACKAGE_MEMBER_CHANGED', 'An owning package member differs from its manifest.',
                            details={'owner_folder': folder, 'member': row['path']})
    return manifest


def build_universal_plugin_architecture(plugin_root, *, registry=None):
    from .sector_support import (
        IMPLEMENTED_SECTORS,
        sector_actions,
        sector_package_contract,
        sector_package_folder,
    )
    root = Path(plugin_root).resolve()
    if root != Path(__file__).resolve().parents[2]:
        raise LaneError('PACKAGE_SOURCE_MISMATCH', 'Load the owning package before projecting its executable registry.')
    if registry is None:
        from .engine import Engine
        with tempfile.TemporaryDirectory(prefix='evidence-lane-architecture-') as temporary:
            return build_universal_plugin_architecture(root, registry=Engine(Path(temporary)).registry)
    actions = registry.schemas()
    owners = []
    for lane_id in CANONICAL_LANE_IDS:
        lane = get_lane(lane_id)
        item = {'surface_id': lane_id, 'surface_kind': lane.kind, 'folder': lane.folder,
                'database': lane.database_relative_path, 'files': lane.files_relative_path,
                'schema': lane_schema_asset(lane_id), 'artifacts': lane_artifact_contract(lane_id),
                'source_origins': list(lane.source_origins),
                'actions': [row for row in actions if row['profile'] == lane_id],
                'installation_verified': False}
        if lane_id in AUTHORITY_LANE_IDS:
            folder = authority_package_folder(lane_id)
            relative = folder + '/manifest.v4.json'
            manifest = _validated_owner_package(root, folder)
            expected = authority_package_contract(lane_id, registry)
            if any(manifest.get(key) != value for key, value in expected.items()):
                raise LaneError('AUTHORITY_PACKAGE_STALE', 'Regenerate the authority package from its actual runtime migration contract.', details={'authority_id': lane_id})
            item['runtime'] = expected['runtime_module']
            item['source_package_folder'] = folder
            item['actions'] = expected['actions']
            item['manifest'] = {'path': relative, 'sha256': sha256_file(root / relative)}
        elif lane_id in IMPLEMENTED_SECTORS:
            relative = sector_package_folder(lane_id) + '/manifest.v4.json'
            manifest = _read_json(root / relative)
            expected = sector_package_contract(lane_id, registry)
            if any(manifest.get(key) != value for key, value in expected.items()):
                raise LaneError('SECTOR_PACKAGE_STALE', 'Regenerate this sector package from its owning runtime.', details={'lane_id': lane_id})
            for member in manifest['members']:
                path = root / member['path']
                if not path.resolve().is_relative_to(root / sector_package_folder(lane_id)) or sha256_file(path).lower() != member['sha256']:
                    raise LaneError('SECTOR_PACKAGE_MEMBER_CHANGED', 'A sector package member differs from its manifest.')
            item['actions'] = sector_actions(registry, lane_id)
            item['runtime'] = expected['runtime_module']
            item['source_package_folder'] = expected['source_package_folder']
            item['manifest'] = {'path': relative, 'sha256': sha256_file(root / relative)}
        owners.append(item)
    workflow_owners = []
    for owner_id in ('project_authority', 'instructions'):
        folder = 'authorities/' + owner_id
        manifest = _validated_owner_package(root, folder)
        workflow = _read_json(root / folder / 'workflow.v4.json')
        if workflow['actions'] != authority_actions(registry, owner_id):
            raise LaneError('AUTHORITY_PACKAGE_STALE', 'Regenerate this workflow owner from the current registry.')
        workflow_owners.append({'owner_id': owner_id, 'source_package_folder': folder,
            'manifest_sha256': sha256_file(root / folder / 'manifest.v4.json'),
            'storage': workflow['storage'], 'actions': workflow['actions'],
            'logical_authority_added': False, 'installed_execution_claimed': False})
    policy = _read_json(root / 'env/orchestration.policy.v4.json')
    installation = _read_json(
        root / 'authorities/session_authority/installation-layout.v4.json'
    )
    result = {'schema': PLUGIN_ARCHITECTURE_SCHEMA, 'authority': 'current_executable_registry_and_lane_contracts',
        'actions': actions, 'workflows': registry.workflow_schemas(), 'surfaces': owners,
        'workflow_owners': workflow_owners,
        'hooks': hook_registry(), 'operating_cycle': list(OPERATING_CYCLE_STAGES),
        'routing_stages': list(UNIVERSAL_ROUTING_STAGES), 'memory_authorities': list(MEMORY_AUTHORITY_MAP),
        'root_pv': {'folder': PROJECT_COORDINATION_FOLDER, 'role': 'identity_and_coordinated_lane_head_references',
                    'business_database': False, 'manifest_sha256': sha256_file(root / PROJECT_COORDINATION_FOLDER / 'manifest.v4.json')},
        'tool_roles': list(TOOL_ROLE_CLASSES),
        'action_tool_requirements': [{'action': row['name'], 'required_tools': row['required_tools'],
            'worker_operations': row['worker_operations']} for row in actions],
        'tool_installation_or_execution_inferred': False,
        'orchestration_policy': policy, 'installation_contract': installation,
        'runtime_project_databases_packaged': False, 'installed_execution_claimed': False}
    result['contract_sha256'] = sha256_bytes(canonical_json_bytes(result))
    return result


def _load_or_build_architecture(architecture=None):
    return architecture if architecture is not None else build_universal_plugin_architecture(resolve_plugin_root(__file__))


def _graph(identity, *, role='EXECUTABLE_WORKFLOW'):
    from .graph_pipeline import SemanticGraph
    return SemanticGraph(identity, direction='TB', role=role)


def _master_semantic_graph(architecture):
    graph = _graph('universal_plugin_architecture')
    stages = architecture['routing_stages']
    previous = None
    for ordinal, item in enumerate(stages):
        key = 'STAGE_' + str(ordinal)
        graph.add_node(key, item['stage'].replace('_', ' ') + '\n' + item['source'], 'semantic')
        if previous:
            graph.add_edge(previous, key)
        previous = key
    graph.add_node('ROOT_PV', 'project evidence head coordinator: exact lane heads and references', 'root')
    graph.add_edge('STAGE_6', 'ROOT_PV')
    for surface in architecture['surfaces']:
        key = _safe_graph_id('LANE', surface['surface_id'])
        graph.add_node(key, surface['folder'] + '\nSQLite and owning files', 'semantic')
        graph.add_edge('ROOT_PV', key, 'exact selected head')
    return graph


def render_universal_architecture_mmd(architecture=None):
    return _master_semantic_graph(_load_or_build_architecture(architecture)).render_mermaid()[0]


def render_universal_architecture_dot(architecture=None):
    return _master_semantic_graph(_load_or_build_architecture(architecture)).render_dot()[0]


def build_dedicated_skill_workflows(architecture):
    return {'schema': 'evidence-lane.dedicated-skill-workflows.v4',
        'skills': [{'skill': workflow['skill'], 'workflow': workflow['name'],
            'actions': workflow['actions'], 'steps': architecture['routing_stages'],
            'action_order': 'selected_by_user_intent_and_current_contract',
            'installation_verified': False} for workflow in architecture['workflows']]}


def render_dedicated_skill_workflow(skill):
    graph = _graph(_safe_graph_id('skill', skill['skill']))
    graph.add_node('SKILL', skill['skill'], 'root')
    graph.add_node('REGISTRY', 'Current registered operation contract', 'semantic')
    graph.add_node('ENGINE', 'Native SDK/MCP to persistent engine API', 'semantic')
    graph.add_node('RESULT', 'Bounded result and attributed evidence', 'output')
    graph.add_edge('SKILL', 'REGISTRY')
    graph.add_edge('REGISTRY', 'ENGINE')
    for action in skill['actions']:
        key = _safe_graph_id('ACTION', action['name'])
        graph.add_node(key, action['name'] + '\n' + action['permission'], 'semantic')
        graph.add_edge('ENGINE', key, 'when selected', conditional=True)
        graph.add_edge(key, 'RESULT')
    return graph.render_pair()


def build_surface_workflows(architecture):
    return {'schema': 'evidence-lane.surface-workflows.v4', 'surfaces': architecture['surfaces']}


def render_surface_workflow(surface):
    graph = _graph(_safe_graph_id('surface', surface['surface_id']))
    for key, label, kind in (
        ('CONTRACT', surface['surface_id'] + ' owning contract', 'root'),
        ('ADMISSION', 'Selected project, grant and current lane head', 'semantic'),
        ('EXECUTE', 'Bounded owning operation and selected tools', 'semantic'),
        ('VERIFY', 'Verify outputs and source identities', 'semantic'),
        ('SQLITE', surface['database'], 'output'),
        ('FILES', surface['files'] + ': declared output formats only', 'output'),
        ('ROOT_PV', 'Coordinated project evidence head coordinator reference', 'output')):
        graph.add_node(key, label, kind)
    for left, right in [('CONTRACT', 'ADMISSION'), ('ADMISSION', 'EXECUTE'), ('EXECUTE', 'VERIFY'),
                        ('VERIFY', 'SQLITE'), ('VERIFY', 'FILES'), ('SQLITE', 'ROOT_PV'), ('FILES', 'ROOT_PV')]:
        graph.add_edge(left, right)
    return graph.render_pair()


def _memory_architecture_graph():
    graph = _graph('memory_authorities', role='AUTHORITY_TRAVERSAL')
    graph.add_node('INPUT', 'Classified visible input or verified result', 'root')
    for ordinal, item in enumerate(MEMORY_AUTHORITY_MAP):
        key = 'OWNER_' + str(ordinal)
        graph.add_node(key, item['memory_class'] + '\n' + ', '.join(item['owners']), 'semantic')
        graph.add_edge('INPUT', key, 'owning workflow only', conditional=True)
    return graph


def render_memory_architecture_mmd():
    return _memory_architecture_graph().render_mermaid()[0]


def render_memory_architecture_dot():
    return _memory_architecture_graph().render_dot()[0]
