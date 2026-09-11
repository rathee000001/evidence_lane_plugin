"""Project the executable v4 registry into canonical SDK, MCP and schema assets.

This replaces the admitted v3 AST catalog bootstrap. It never starts a service,
installs dependencies, creates a project, executes actions or deletes old files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / 'src'))

from evidence_lane_plugin.capture_routing import HookEnvelope
from evidence_lane_plugin.connections import ConnectRequest
from evidence_lane_plugin.current_route_registry import current_implementation_registry
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.lanes import RETIRED_OFFICE_LANE_IDS
from evidence_lane_plugin.mcp_adapter import tool_from_action
from evidence_lane_plugin.registry import WORKFLOWS
from evidence_lane_plugin.remote_api import (
    DiscoverRequest,
    ProbeRequest,
    ProbeVerification,
    RemoteAction,
    RemoteCapture,
    RemoteConnect,
    RemoteConnection,
    RemotePolicy,
)
from evidence_lane_plugin.remote_transport import RemoteClientConfig
from evidence_lane_plugin.sdk import ActionRequest, ActionResponse
from evidence_lane_plugin.workflow_surface import skill_action_reference


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def _member(relative, outputs):
    if relative in outputs:
        return {'path': relative, 'sha256': digest(outputs[relative])}
    path = PLUGIN_ROOT / relative
    if not path.is_file():
        raise RuntimeError('A declared distribution member is missing: ' + relative)
    return {'path': relative, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _family(name, purpose, sources, outputs):
    return {'schema': 'evidence-lane.schema-family.v4', 'family': name, 'purpose': purpose,
        'members': [_member(path, outputs) for path in sorted(sources)],
        'complete_owner_files_indexed': True, 'runtime_execution_inferred': False}


def _owner_schema_members(folder):
    selected = []
    names = {'schema.sql', 'sqlite-schema.v4.json', 'schema-template.json', 'migration-history.json',
             'lane-contract.v4.json', 'pointer-contract.v4.json', 'runtime-binding.v4.json',
             'manifest.v4.json'}
    for path in sorted((PLUGIN_ROOT / folder).glob('*')):
        if path.is_file() and (path.name in names or path.name.endswith('.schema.json')):
            selected.append(path.relative_to(PLUGIN_ROOT).as_posix())
    if not selected:
        raise RuntimeError('An owning package has no schema members: ' + folder)
    return selected


def exports(registry):
    actions = registry.schemas()
    identity = digest(actions)
    envelope = {'authority': 'engine_typed_action_registry', 'action_set_sha256': identity,
                'installed_execution_claimed': False}
    outputs = {
        'schemas/action-request.v4.schema.json': ActionRequest.model_json_schema(),
        'schemas/action-response.v4.schema.json': ActionResponse.model_json_schema(),
        'schemas/public-action-schemas.v4.json': {'schema': 'evidence-lane.public-action-schemas.v4',
            **envelope, 'actions': actions},
        'sdk/routing/current-route-registry.v4.json': current_implementation_registry(registry),
    }
    outputs.update({f'skills/{workflow.skill}/references/actions.json': skill_action_reference(registry, workflow.name)
                    for workflow in WORKFLOWS})
    outputs['toolchains/operation-toolchains.v4.json'] = {
        'schema_version': 4, **envelope, 'operation_count': len(actions),
        'installation_matrix': 'toolchains/shared-toolchain.v4.json',
        'selection_authority': 'each_registered_operation_not_automatic_per_lane_bundles',
        'operations': [action['toolchain'] for action in actions]}
    # Keep the retained runtime workflow documents on the same executable
    # registry as the outer SDK/MCP assets. They are consumed by local clients.
    for path in sorted((PLUGIN_ROOT/'src/evidence_lane_plugin/contracts').glob('*.workflow.v4.json')):
        prior = json.loads(path.read_text(encoding='utf-8'))
        owner = path.name.removesuffix('.workflow.v4.json')
        if owner in RETIRED_OFFICE_LANE_IDS:
            # Scope-retired files are preserved only for the final purge audit;
            # they cannot reintroduce executable SDK or MCP registrations.
            continue
        if owner == 'artifacts':
            selected = [item for item in actions if item['name'].startswith('lane_view_')]
        elif owner == 'continuity':
            selected = [item for item in actions if item['name'].startswith('continuation_')]
        elif owner == 'projects':
            selected = [item for item in actions if item['profile'] in {'projects', 'universe'}]
        else:
            selected = [item for item in actions if item['profile'] == owner]
        if not selected:
            raise RuntimeError('A retained workflow lacks current registry actions: ' + owner)
        models = {model.__name__:model.model_json_schema() for item in selected
                  for model in (registry.get(item['name']).input_model, registry.get(item['name']).output_model)}
        outputs[path.relative_to(PLUGIN_ROOT).as_posix()] = {
            **prior, **envelope, 'authority':'separate_owning_lane_sqlite_with_root_pv_coordination',
            'generated_from':'typed Python registry contracts', 'contracts':models, 'actions':selected,
        }
        document = outputs[path.relative_to(PLUGIN_ROOT).as_posix()]
        if owner == 'artifacts':
            document['views'] = registry.view_schemas()
        if owner == 'projects':
            document['eligible_owner_reads'] = [item['name'] for item in actions if item['cross_project_read']]
    transports = {'local-connect': ConnectRequest, 'hook-envelope': HookEnvelope,
        'remote-discover': DiscoverRequest, 'remote-connect': RemoteConnect, 'remote-connection': RemoteConnection,
        'remote-action': RemoteAction, 'remote-capture': RemoteCapture, 'remote-policy': RemotePolicy,
        'remote-client-config': RemoteClientConfig, 'remote-probe': ProbeRequest, 'remote-probe-verification': ProbeVerification}
    outputs.update({f'schemas/transports/{name}.v4.schema.json': model.model_json_schema()
                    for name, model in transports.items()})
    sdk, mcp, schemas = [], [], []
    for action in actions:
        name = action['name']
        schema_path = f'schemas/actions/{name}.v4.schema.json'
        sdk_path = f'sdk/actions/{name}.action.v4.json'
        mcp_path = f'mcp/actions/{name}.binding.v4.json'
        outputs[schema_path] = {'schema': 'evidence-lane.action-schema.v4', **envelope, **action}
        outputs[sdk_path] = {'schema': 'evidence-lane.sdk-action.v4', **envelope,
            'action': name, 'contract': schema_path, 'contract_sha256': digest(outputs[schema_path]),
            'client': 'evidence_lane_plugin.sdk.EvidenceLaneClient.call',
            'request_envelope': 'schemas/action-request.v4.schema.json',
            'response_envelope': 'schemas/action-response.v4.schema.json',
            'automatic_mutation_retry': False}
        outputs[mcp_path] = {'schema': 'evidence-lane.mcp-binding.v4', **envelope,
            'action': name, 'sdk_binding': sdk_path, 'sdk_binding_sha256': digest(outputs[sdk_path]),
            'tool': tool_from_action(action).model_dump(mode='json', exclude_none=True),
            'execution_owner': 'connected_engine_registry', 'adapter_executes_project_operations': False}
        schemas.append(schema_path)
        sdk.append(sdk_path)
        mcp.append(mcp_path)

    outputs['sdk/actions/action-manifest.v4.json'] = {
        'schema': 'evidence-lane.sdk-action-manifest.v4', **envelope,
        'action_count': len(actions),
        'members': [{'path': item, 'sha256': digest(outputs[item])} for item in sdk]}
    outputs['schemas/actions/action-manifest.v4.json'] = {
        'schema': 'evidence-lane.action-schema-manifest.v4', **envelope,
        'action_count': len(actions),
        'members': [{'path': item, 'sha256': digest(outputs[item])} for item in schemas]}
    outputs['mcp/mcp-manifest.v4.json'] = {
        'schema': 'evidence-lane.mcp-manifest.v4', **envelope,
        'action_count': len(actions),
        'members': [{'path': item, 'sha256': digest(outputs[item])} for item in mcp]}

    family_paths = []
    authority_root = PLUGIN_ROOT / 'authorities'
    for owner in sorted(path.name for path in authority_root.iterdir()
                        if path.is_dir() and path.name != 'project_sectors'):
        relative = f'schemas/authorities/{owner}.v4.json'
        outputs[relative] = _family(owner, 'Complete current authority owner schema and contract index',
            _owner_schema_members(f'authorities/{owner}'), outputs)
        family_paths.append(relative)
    sector_root = authority_root / 'project_sectors'
    for owner in sorted(path.name for path in sector_root.iterdir() if path.is_dir()):
        relative = f'schemas/project-sectors/{owner}.v4.json'
        outputs[relative] = _family(owner, 'Complete current sector owner schema and contract index',
            _owner_schema_members(f'authorities/project_sectors/{owner}'), outputs)
        family_paths.append(relative)

    static_families = {
        'env': ('Locked environment policy and current executable bindings', [
            'env/SESSION_FLASH_MANIFEST.json', 'env/SOURCE_PACKET_AUDIT.json',
            'env/codex-environment-policy.v4.json',
            'src/evidence_lane_plugin/codex_action_plane.py',
            'src/evidence_lane_plugin/codex_env_uop_policy.py']),
        'uop': ('Locked operation policy, gates and typed execution bindings', [
            'uop/uop_sqlite.sqlite', 'uop/codex-operation-policy.v4.json',
            'src/evidence_lane_plugin/codex_action_plane.py',
            'src/evidence_lane_plugin/env_uop_tool_routing.py',
            'src/evidence_lane_plugin/codex_env_uop_policy.py']),
        'mcp': ('Thin native MCP adapter over the complete generated action bindings', [
            '.mcp.json', 'mcp/mcp-manifest.v4.json', 'src/evidence_lane_plugin/mcp_adapter.py',
            'src/evidence_lane_plugin/mcp_server.py']),
        'sdk': ('Outer typed SDK, internal dispatcher and complete executable family bindings', [
            'schemas/action-request.v4.schema.json', 'schemas/action-response.v4.schema.json',
            'sdk/evidence_lane_sdk.py', 'sdk/actions/action-manifest.v4.json',
            'src/evidence_lane_plugin/sdk.py']),
        'routing': ('Current action, client, host and project route contracts', [
            'sdk/routing/current-route-registry.v4.json', 'sdk/routing/router.py',
            'src/evidence_lane_plugin/current_route_registry.py', 'src/evidence_lane_plugin/host_routing.py']),
        'package': ('Plugin identity and source-derived architecture contracts', [
            '.codex-plugin/plugin.json', 'manifests/plugin-architecture.v4.json',
            'src/evidence_lane_plugin/plugin_architecture.py']),
        'governance': ('Current orchestration, lane and transition policies', [
            'env/orchestration.policy.v4.json', 'src/evidence_lane_plugin/state_law.py',
            'src/evidence_lane_plugin/tool_routes.py']),
        'lifecycle': ('Session, project Plan and bounded work lifecycle owners', [
            'src/evidence_lane_plugin/session.py', 'src/evidence_lane_plugin/plan_runtime.py',
            'src/evidence_lane_plugin/adaptive_delta_entry.py',
            'src/evidence_lane_plugin/adaptive_delta_exit.py']),
        'plan': ('Authoritative project Plan database and exact full host Plan projection', [
            'authorities/plan/schema.sql', 'authorities/plan/migration-history.json',
            'sdk/plan/runtime.py', 'src/evidence_lane_plugin/plan_runtime.py']),
        'source-intake': ('Source authority, preparation and materialization contracts', [
            'authorities/source_authority/schema.sql', 'src/evidence_lane_plugin/source_intake.py',
            'src/evidence_lane_plugin/source_preparation.py',
            'src/evidence_lane_plugin/source_materialization.py']),
        'work-handoff': ('Exact client continuation and recovery contracts', [
            'src/evidence_lane_plugin/task_binding_registry.py',
            'src/evidence_lane_plugin/session_authority.py']),
        'toolchains': ('Operation-specific tool selection, fallbacks and readiness contracts', [
            'toolchains/tool-catalog.v4.json', 'toolchains/tool-definitions.v4.json',
            'toolchains/operation-toolchains.v4.json', 'src/evidence_lane_plugin/tool_routes.py']),
        'schema-authority': ('Complete generated schema catalog and executable source owners', [
            'schemas/actions/action-manifest.v4.json', 'scripts/generate_public_schema_catalog.py']),
        'consumers': ('Studio and inspectable package architecture consumers', [
            'manifests/plugin-architecture.v4.json', 'src/evidence_lane_plugin/studio_gateway.py']),
        'recovery': ('Coherent separate-lane backup, restore and reconciliation contracts', [
            'src/evidence_lane_plugin/database_recovery.py',
            'src/evidence_lane_plugin/job_recovery.py']),
        'project-memory': ('Attributed project-memory storage and retrieval contracts', [
            'authorities/project_memory/schema.sql', 'src/evidence_lane_plugin/project_memory.py']),
        'task-exchange': ('Typed task evidence exchange contracts', [
            'authorities/canon_input/schema.sql', 'src/evidence_lane_plugin/canon_task_graph.py',
            'src/evidence_lane_plugin/canon_runtime_continuity.py']),
        'project-evidence-map': ('Per-project links and cross-project hash-only network contracts', [
            'authorities/project_universe/schema.sql', 'src/evidence_lane_plugin/project_universe.py',
            'src/evidence_lane_plugin/universe_snapshot.py',
            'src/evidence_lane_plugin/universe_federation.py']),
        'projects': ('Project registration, selection, grants and separate storage layout', [
            'authorities/project_authority/schema.sql', 'src/evidence_lane_plugin/projects.py',
            'src/evidence_lane_plugin/project_actions.py']),
        'host': ('Measured host observation and exact local Plan-file binding contracts', [
            'sdk/host/runtime.py', 'sdk/plan/runtime.py', 'src/evidence_lane_plugin/host_routing.py']),
    }
    for name, (purpose, sources_) in static_families.items():
        relative = f'schemas/{name}/{name}-family.v4.json'
        outputs[relative] = _family(name, purpose, sources_, outputs)
        family_paths.append(relative)

    generated_families = {
        'actions': ['schemas/actions/action-manifest.v4.json'],
        'hooks': ['schemas/hooks/hook-family.v4.json'],
        'install': sorted(path.relative_to(PLUGIN_ROOT).as_posix()
                          for path in (PLUGIN_ROOT / 'schemas/install').glob('*.json')),
        'skills': ['schemas/skills/skill-registry.v4.json'],
        'transports': sorted(path.relative_to(PLUGIN_ROOT).as_posix()
                             for path in (PLUGIN_ROOT / 'schemas/transports').glob('*.json')),
    }
    for name, sources_ in generated_families.items():
        relative = f'schemas/{name}/{name}-family.v4.json'
        if relative not in sources_:
            outputs[relative] = _family(name, f'Complete {name} schema family', sources_, outputs)
        family_paths.append(relative)
    family_paths = sorted(set(family_paths))
    outputs['schemas/schema-family-registry.v4.json'] = {
        'schema': 'evidence-lane.schema-family-registry.v4', **envelope,
        'family_count': len(family_paths),
        'families': [_member(path, outputs) for path in family_paths],
        'owner_packages_remain_canonical': True,
        'duplicate_runtime_databases_packaged': False,
        'removed_families': ['formula', 'project-overlay', 'accepted-pv-zip', 'pv-rollback',
                             'pv-learning-hil', 'connector-brain'],
    }

    sdk_families = {
        'actions': 'sdk/actions/dispatcher.py',
        'authorities': 'sdk/authorities/runtime.py',
        'delta': 'sdk/delta/runtime.py',
        'env_uop': 'sdk/env_uop/runtime.py',
        'hooks': 'sdk/hooks/runtime.py',
        'host': 'sdk/host/runtime.py',
        'internal': 'sdk/internal/runtime.py',
        'plan': 'sdk/plan/runtime.py',
        'recovery': 'sdk/recovery/runtime.py',
        'routing': 'sdk/routing/router.py',
        'skills': 'sdk/skills/runtime.py',
        'transports': 'sdk/transports/runtime.py',
        'workflows': 'sdk/workflows/runtime.py',
    }
    outputs['sdk/sdk-surface-registry.v4.json'] = {
        'schema': 'evidence-lane.sdk-surface-registry.v4', **envelope,
        'family_count': len(sdk_families), 'action_count': len(actions),
        'families': [{'name': name, **_member(path, outputs)}
                     for name, path in sorted(sdk_families.items())],
        'per_action_manifest': 'sdk/actions/action-manifest.v4.json',
        'automatic_mutation_retry': False,
        'removed_families': [{'name': 'rollback',
            'reason': 'Accepted-PV and ZIP rollback were explicitly removed; current Git and database recovery use sdk/recovery.'}],
    }

    def complete_manifest(prefix, manifest_path, schema_name):
        paths = {path.relative_to(PLUGIN_ROOT).as_posix()
                 for path in (PLUGIN_ROOT / prefix).rglob('*')
                 if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}
        paths.update(path for path in outputs if path.startswith(prefix + '/'))
        paths.discard(manifest_path)
        return {'schema': schema_name, **envelope, 'action_count': len(actions),
            'member_count': len(paths), 'members': [_member(path, outputs) for path in sorted(paths)],
            'complete_family_surface': True}

    outputs['sdk/sdk-manifest.v4.json'] = complete_manifest(
        'sdk', 'sdk/sdk-manifest.v4.json', 'evidence-lane.sdk-manifest.v4')
    outputs['schemas/schema-manifest.v4.json'] = complete_manifest(
        'schemas', 'schemas/schema-manifest.v4.json', 'evidence-lane.schemas-manifest.v4')
    outputs['manifests/executable-action-surface.v4.json'] = {
        'schema': 'evidence-lane.executable-action-surface.v4', **envelope,
        'generator': 'scripts/generate_public_schema_catalog.py',
        'actions': [item['name'] for item in actions],
        'scope': 'current_registered_actions_and_transport_envelopes; lane implementation and installation qualify separately',
        'members': [{'path': path, 'sha256': digest(value)} for path, value in sorted(outputs.items())],
    }
    return outputs


def generate(registry, root=PLUGIN_ROOT, *, check=False):
    root = Path(root).resolve()
    changed = []
    outputs = exports(registry)
    for relative, value in outputs.items():
        target = root / relative
        body = encoded(value)
        if target.is_file() and target.read_bytes() == body:
            continue
        changed.append(relative)
        if not check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
    if check and changed:
        raise RuntimeError('Current registry projections require regeneration: ' + ', '.join(changed))
    return {'action_count': len(registry.schemas()), 'generated_members': len(outputs),
            'changed_members': len(changed), 'check': check, 'installed_execution_claimed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='evi-registry-projection-') as directory:
        registry = Engine(Path(directory)).registry
        registry.freeze()
        print(json.dumps(generate(registry, check=arguments.check)))


if __name__ == '__main__':
    main()
