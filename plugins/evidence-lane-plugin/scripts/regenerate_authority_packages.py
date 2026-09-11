"""Project current authority behavior into its original owning source packages."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / 'src'))

from authority_package_assets import compile_authority_assets, compile_workflow_assets, schema_graph
from sector_package_assets import empty_schema_from_migrations, encode, schema_bytes

from evidence_lane_plugin.authority_support import (
    AUTHORITY_SUPPORT_PROFILES,
    PROJECT_COORDINATION_FOLDER,
    authority_migrations,
    authority_package_contract,
    authority_package_folder,
)
from evidence_lane_plugin.hook_contract import hook_registry
from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS, get_lane
from evidence_lane_plugin.session_authority import SESSION_MIGRATIONS, SessionResult
from evidence_lane_plugin.storage import APPLICATION_ID, CORE_SCHEMA, LANE_SCHEMA, RECEIPTS_SCHEMA
from evidence_lane_plugin.writers import WRITER_MIGRATIONS


def sql_bytes(migrations, foundation=''):
    text = '-- Projection: runtime applies the real ordered migrations under its project writer.\n' + foundation
    return (text + '\n' + '\n'.join(f'-- {item.owner} v{item.version}, digest {item.digest}\n' +
        '\n'.join(statement.rstrip(';') + ';' for statement in item.statements) for item in migrations) + '\n').encode()


def manifest(outputs, folder, contract):
    contract = dict(contract)
    contract['members'] = [{'path': path, 'sha256': hashlib.sha256(raw).hexdigest()}
                           for path, raw in sorted(outputs.items()) if path.startswith(folder + '/')]
    outputs[folder + '/manifest.v4.json'] = encode(contract)
    return {'path': folder + '/manifest.v4.json',
            'sha256': hashlib.sha256(outputs[folder + '/manifest.v4.json']).hexdigest()}


def generate(registry, *, check=False):
    outputs, authorities = {}, []
    for lane_id in AUTHORITY_SUPPORT_PROFILES:
        folder = authority_package_folder(lane_id)
        migrations = authority_migrations(lane_id)
        outputs[folder + '/schema.sql'] = sql_bytes(migrations, RECEIPTS_SCHEMA if lane_id == 'receipts' else '')
        outputs[folder + '/migration-history.json'] = encode({
            'schema': 'evidence-lane.packaged-migration-history.v4', 'authority_id': lane_id,
            'migrations': [{'owner': item.owner, 'version': item.version, 'digest': item.digest,
                'description': item.description, 'statements': item.statements} for item in migrations]})
        outputs[folder + '/runtime.py'] = f'''"""{lane_id}: the actual owner implementation and separate project store."""
from evidence_lane_plugin.authority_support import (
    canonical_authority_module,
    refresh_authority_support,
    validate_authority_support,
)

AUTHORITY_ID = {lane_id!r}


def canonical_module():
    return canonical_authority_module(AUTHORITY_ID)


def initialize(project, *, writer):
    return refresh_authority_support(project, AUTHORITY_ID, writer=writer)


def inspect(project):
    return validate_authority_support(project, AUTHORITY_ID)
'''.encode()
        outputs.update(compile_authority_assets(registry, lane_id))
        contract = authority_package_contract(lane_id, registry)
        if lane_id == 'plan':
            outputs[folder + '/law.v4.json'] = encode({
                'schema': 'evidence-lane.plan-law.v4', 'database': get_lane(lane_id).database_relative_path,
                'source': ['plan_runtime', 'steering', 'jobs', 'adaptive_delta_entry', 'adaptive_delta_exit'],
                'current_revision': 'One current revision; immutable definitions and append-only attributed transitions',
                'semantic_steer': 'Checkpoint affected work and replace active/queued contracts atomically; reject stale completion',
                'completion': 'Exact task/revision/contract, accepted checks, succeeded job and delta_exit_verified receipt',
                'logical_authority': 'plan', 'native_step_list_is_project_plan': False,
                'arbitrary_status_mutation': False, 'automatic_hil': False})
        elif lane_id == 'chat_lineage':
            outputs[folder + '/law.v4.json'] = encode({
                'schema': 'evidence-lane.chatlineage-law.v4', 'database': get_lane(lane_id).database_relative_path,
                'files': get_lane(lane_id).files_relative_path,
                'source': ['lineage', 'capture_routing', 'task_binding_registry', 'prompt_index', 'codex_turn_control'],
                'hooks': hook_registry(),
                'binding': 'Explicit selected project/client/session and attributed visible input; no CWD fallback',
                'order': 'Recorded arrival sequence and hash cursor; not an inferred total native execution order',
                'content': 'Redact before storage and hashing; private reasoning and arbitrary media bytes excluded',
                'native_task_attestation_inferred': False, 'automatic_recall_import': False})
        if lane_id == 'canon':
            outputs[folder + '/consequence_graph/manifest.schema.json'] = schema_bytes({'type': 'object',
                'required': ['schema', 'owner', 'database', 'separate_graph_database', 'runtime', 'members'],
                'properties': {'owner': {'const': 'canon'}, 'separate_graph_database': {'const': False}}},
                'Canon consequence graph binding to its parent authority')
            manifest(outputs, folder + '/consequence_graph', {
                'schema': 'evidence-lane.canon-consequence-package.v4', 'owner': 'canon',
                'database': get_lane(lane_id).database_relative_path, 'separate_graph_database': False,
                'runtime': ['evidence_lane_plugin.canon_consequence_graph', 'evidence_lane_plugin.canon_task_graph'],
                'installed_execution_claimed': False})
        outputs[folder + '/manifest.schema.json'] = schema_bytes({'type': 'object',
            'required': [*contract, 'members', 'runtime_binding'],
            'properties': {'schema': {'const': contract['schema']}, 'authority_id': {'const': lane_id},
                'source_package_folder': {'const': folder}, 'project_state_folder': {'const': get_lane(lane_id).folder},
                'runtime_database_packaged': {'const': False}, 'members': {'type': 'array', 'minItems': 1}}},
            lane_id + ' current authority manifest')
        contract['runtime_binding'] = folder + '/runtime.py'
        contract['schema_sql_role'] = 'Actual owner migration projection; the engine creates lane identity and schema history under its writer.'
        authority_manifest = manifest(outputs, folder, contract)
        authorities.append({'authority_id': lane_id, 'runtime_module': contract['runtime_module'],
                            'path': folder, 'manifest': authority_manifest})

    folder = PROJECT_COORDINATION_FOLDER
    extras, _ = compile_workflow_assets(registry, 'project_authority', folder,
        storage='root-pv.sqlite3 for project identity, writer fencing, lane heads and coordinated recovery only',
        runtime_modules=['evidence_lane_plugin.storage', 'evidence_lane_plugin.lane_transactions',
            'evidence_lane_plugin.writers', 'evidence_lane_plugin.coordination', 'evidence_lane_plugin.projects'])
    outputs.update(extras)
    outputs[folder + '/schema.sql'] = sql_bytes(WRITER_MIGRATIONS, CORE_SCHEMA)
    outputs[folder + '/lane-metadata.sql'] = LANE_SCHEMA.encode()
    raw, schema = empty_schema_from_migrations('project_authority', WRITER_MIGRATIONS,
        foundation=CORE_SCHEMA, kind='root-coordination', application_id=APPLICATION_ID,
        schema_seed_rows={'root_pv_head': [(1, 0, '', None)]})
    outputs[folder + '/project-authority.sqlite'] = raw
    outputs[folder + '/sqlite-schema.v4.json'] = encode(schema)
    mmd, dot, _ = schema_graph('project_authority', schema)
    outputs[folder + '/project_authority.mmd'] = mmd.encode()
    outputs[folder + '/project_authority.dot'] = dot.encode()
    outputs[folder + '/runtime.py'] = b'''"""Project coordination binds the sole Root PV and the existing project writer."""
from evidence_lane_plugin.coordination import ProjectCoordinator
from evidence_lane_plugin.lane_transactions import coordinated_transaction, recover_transactions
from evidence_lane_plugin.storage import ProjectStore, project_snapshot
from evidence_lane_plugin.writers import WriterLease

__all__ = ['ProjectCoordinator', 'ProjectStore', 'WriterLease', 'coordinated_transaction',
           'project_snapshot', 'recover_transactions']
'''
    layout = {'schema': 'evidence-lane.live-project-root-layout.v4', 'source_package_folder': folder,
        'root_database': 'root-pv.sqlite3', 'root_business_records': False,
        'authority_count': 8, 'lanes': [{'lane_id': name, 'kind': get_lane(name).kind,
            'folder': get_lane(name).folder, 'database': get_lane(name).database_relative_path,
            'files': get_lane(name).files_relative_path, 'schema_history': get_lane(name).schema_history_relative_path}
            for name in CANONICAL_LANE_IDS],
        'separate_source_workspace': True, 'shared_tool_installation': True,
        'coordinated_publish': 'Exact lane heads, commit journal, publication fence and owned recovery',
        'accepted_zip_hierarchy': False, 'extra_authority_database': False}
    outputs[folder + '/live-root-layout.v4.json'] = encode(layout)
    outputs[folder + '/live-root-layout.schema.json'] = schema_bytes({'const': layout}, 'Separate external project layout')
    root_contract = {'schema': 'evidence-lane.root-pv-package.v4', 'source_package_folder': folder,
        'database': 'root-pv.sqlite3', 'runtime': folder + '/runtime.py', 'business_records': False,
        'roles': ['project_identity', 'writer_fence', 'lane_catalog', 'published_lane_heads', 'coordinated_commit_recovery'],
        'accepted_zip_hierarchy': False, 'installed_execution_claimed': False}
    outputs[folder + '/manifest.schema.json'] = schema_bytes({'type': 'object', 'required': [*root_contract, 'members']}, 'Project coordination manifest')
    manifest(outputs, folder, root_contract)

    auxiliaries = []
    for owner, modules, storage in (
        ('session_authority', ['evidence_lane_plugin.session_authority'], get_lane('receipts').database_relative_path + ' sessions schema owner'),
        ('instructions', ['evidence_lane_plugin.agent_configuration'], 'Scoped instruction and separate workspace/host recall files; no database'),
    ):
        folder = 'authorities/' + owner
        extras, _ = compile_workflow_assets(registry, owner, folder, storage=storage, runtime_modules=modules)
        outputs.update(extras)
        if owner == 'session_authority':
            outputs[folder + '/runtime.py'] = b'''"""Boot, Resume and locked Flash retain the owning session implementation."""
from evidence_lane_plugin.session_authority import SESSION_MIGRATIONS, SessionAuthority

__all__ = ['SESSION_MIGRATIONS', 'SessionAuthority']
'''
            outputs[folder + '/schema.sql'] = sql_bytes(SESSION_MIGRATIONS)
            _, schema = empty_schema_from_migrations(owner, SESSION_MIGRATIONS,
                foundation=LANE_SCHEMA + RECEIPTS_SCHEMA, kind='session-owner-subset')
            outputs[folder + '/sqlite-schema.v4.json'] = encode(schema | {'template_file_packaged': False,
                'owning_lane': 'receipts', 'separate_database': False})
            mmd, dot, _ = schema_graph(owner, schema)
            outputs[folder + '/session-authority.mmd'] = mmd.encode()
            outputs[folder + '/session-authority.dot'] = dot.encode()
            outputs[folder + '/pointer-contract.schema.json'] = schema_bytes(
                SessionResult.model_json_schema(), 'Exact session head, locked Flash and owner attribution')
            outputs[folder + '/pointer-contract.v4.json'] = encode({'schema': 'evidence-lane.session-pointer-contract.v4',
                'state_authority': 'receipts_lane_sqlite', 'reference': 'session_id, generation, event_digest and locked Flash',
                'separate_current_file': False, 'native_task_attestation_inferred': False})
        else:
            outputs[folder + '/runtime.py'] = b'''"""Scoped AGENTS instructions and separate recall without import or mutation."""
from evidence_lane_plugin.agent_configuration import (
    InstructionRequest,
    InstructionResult,
    resolve_agent_configuration,
)

__all__ = ['InstructionRequest', 'InstructionResult', 'resolve_agent_configuration']
'''
            contract = {'schema': 'evidence-lane.instruction-arms-contract.v4',
                'agents_md_role': 'scoped_instruction_authority', 'workspace_and_host_memory_role': 'separate_non_authoritative_recall',
                'project_memory_database_role': 'separate_authority', 'automatic_host_memory_import': False,
                'raw_chat_history_loaded': False, 'sqlite_authority_owned': False}
            outputs[folder + '/authority-contract.v4.json'] = encode(contract)
            outputs[folder + '/authority-contract.schema.json'] = schema_bytes({'const': contract}, 'Separate instruction and recall arms')
        if owner == 'session_authority':
            installation_policy = PLUGIN / folder / 'installation-layout.v4.json'
            if not installation_policy.is_file():
                raise RuntimeError('The Session authority installation policy is missing')
            outputs[folder + '/installation-layout.v4.json'] = installation_policy.read_bytes()
        package_contract = {'schema': 'evidence-lane.authority-workflow-package.v4',
            'owner_id': owner, 'source_package_folder': folder, 'modules': modules,
            'storage': storage, 'separate_database': False, 'installed_execution_claimed': False}
        outputs[folder + '/manifest.schema.json'] = schema_bytes({'type': 'object',
            'required': [*package_contract, 'members'], 'properties': {'owner_id': {'const': owner},
                'separate_database': {'const': False}, 'installed_execution_claimed': {'const': False}}},
            owner + ' current workflow package')
        record = manifest(outputs, folder, package_contract)
        auxiliaries.append({'owner_id': owner, 'manifest': record})

    outputs['authorities/authority-surface-registry.v4.json'] = encode({
        'schema': 'evidence-lane.authority-surface-registry.v4', 'authorities': authorities,
        'source': 'evidence_lane_plugin.authority_support.AUTHORITY_SUPPORT_PROFILES',
        'authority_count': len(authorities), 'project_databases_packaged': False,
        'workflow_owners': auxiliaries, 'root_pv': PROJECT_COORDINATION_FOLDER + '/manifest.v4.json'})
    changed = []
    for relative, content in outputs.items():
        path = PLUGIN / relative
        if path.is_file() and path.read_bytes() == content:
            continue
        changed.append(relative)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    if check and changed:
        raise RuntimeError('Authority packages require regeneration: ' + ', '.join(changed))
    return {'authority_count': len(authorities), 'workflow_owner_count': len(auxiliaries),
        'project_coordination_owner': PROJECT_COORDINATION_FOLDER, 'members': len(outputs),
        'changed': changed, 'project_data_touched': False}


def main():
    from evidence_lane_plugin.engine import Engine
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='evidence-lane-authority-package-') as temporary:
        registry = Engine(Path(temporary)).registry
        registry.freeze()
        print(json.dumps(generate(registry, check=options.check)))


if __name__ == '__main__':
    main()
