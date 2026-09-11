"""Compile original authority assets from current engine contracts and migrations."""
from __future__ import annotations

import operator
from functools import reduce
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, create_model
from sector_package_assets import empty_schema_from_migrations, encode, schema_bytes

from evidence_lane_plugin.artifact_contract import ViewPublished, ViewState
from evidence_lane_plugin.authority_support import (
    AUTHORITY_SOURCE_OWNERS,
    authority_actions,
    authority_migrations,
    authority_package_folder,
)
from evidence_lane_plugin.graph_pipeline import SemanticGraph
from evidence_lane_plugin.lanes import get_lane, lane_artifact_contract
from evidence_lane_plugin.sdk import UUID_PATTERN, ActionRequest, ActionResponse
from evidence_lane_plugin.storage import LANE_SCHEMA, RECEIPTS_SCHEMA

# Original useful filenames remain in their source owners. They do not name the
# selected project's live SQLite or its optional published graph files.
ASSET_STEMS = {
    'plan': 'plan', 'chat_lineage': 'chat_lineage', 'canon': 'canon-input',
    'memory': 'memory', 'learning': 'agent-learning', 'sources': 'source_authority',
    'receipts': 'receipt-ledger', 'universe': 'project_universe',
    'project_authority': 'project_authority', 'session_authority': 'session-authority',
    'instructions': 'instructions',
}


def request_schema(registry, actions):
    """Discriminate exact action/argument pairs from the canonical input models."""
    models = []
    for row in actions:
        spec = registry.get(row['name'])
        fields = {'action': (Literal[spec.name], ...), 'arguments': (spec.input_model, ...)}
        if row['project_required']:
            fields['project_id'] = (str, Field(pattern=UUID_PATTERN))
        models.append(create_model('AuthorityRequest_' + spec.name, __base__=ActionRequest, **fields))
    if not models:
        return {'not': {}}
    if len(models) == 1:
        return models[0].model_json_schema()
    return TypeAdapter(Annotated[reduce(operator.or_, models), Field(discriminator='action')]).json_schema()


def schema_graph(identity, schema):
    graph = SemanticGraph('schema_' + identity, role='AUTHORITY_TRAVERSAL')
    names = {row['name'] for row in schema['tables']}
    for row in schema['tables']:
        graph.add_node('t_' + row['name'], row['name'] + '\n' + ', '.join(col['name'] for col in row['columns']), kind='table')
    for row in schema['relations']:
        if row['target'] not in names:
            raise ValueError('Unresolved authority schema relationship: ' + row['target'])
        graph.add_edge('t_' + row['source'], 't_' + row['target'], row['column'])
    return graph.render_pair()


def compile_workflow_assets(registry, owner_id, folder, *, storage, runtime_modules, actions=None, views=()):
    """No direct DB writer or alternate authorization path is packaged here."""
    actions = authority_actions(registry, owner_id) if actions is None else actions
    stem = ASSET_STEMS.get(owner_id, owner_id)
    outputs = {}
    for filename, mutates, method in (('builder.py', True, 'build_authority'), ('reader.py', False, 'read_authority')):
        # Planned operations enter through their retained sector builder and
        # Delta contract, never this ordinary SDK mutation interface.
        selected = [row for row in actions if row['mutates'] is mutates and not row['requires_delta']]
        bindings = {row['name']: row['project_required'] for row in selected}
        outputs[folder + '/' + filename] = (
            f'"""{owner_id}: authenticated public SDK {"mutation" if mutates else "read"} binding."""\n'
            'from evidence_lane_plugin.authority_support import call_authority_action\n\n'
            f'OWNER_ID = {owner_id!r}\nACTIONS = {bindings!r}\n\n\n'
            f'def {method}(client, **request):\n'
            '    return call_authority_action(client, ACTIONS, **request)\n\n'
            f'__all__ = ["OWNER_ID", "{method}"]\n').encode()
        contract_file = 'build-refresh-contract.schema.json' if mutates else 'reader-contract.schema.json'
        outputs[folder + '/' + contract_file] = schema_bytes(request_schema(registry, selected), owner_id + ' typed SDK requests')
    outputs[folder + '/refresh-receipt.schema.json'] = schema_bytes(ActionResponse.model_json_schema(), owner_id + ' attributed response')
    binding = {'owner_id': owner_id, 'modules': runtime_modules, 'storage': storage,
               'public_dispatch': 'evidence_lane_plugin.sdk.EvidenceLaneClient.call', 'alternate_writer': False}
    outputs[folder + '/runtime-binding.schema.json'] = schema_bytes({'const': binding}, owner_id + ' canonical runtime binding')
    outputs[folder + '/runtime-binding.v4.json'] = encode(binding)
    flow = SemanticGraph('workflow_' + owner_id, role='EXECUTABLE_WORKFLOW')
    flow.add_node('sdk', 'Authenticated SDK and native MCP client')
    flow.add_node('read', 'Exact selected scope and bounded recorded evidence')
    flow.add_node('writer', 'Engine coordinator and owning project writer')
    flow.add_node('delta', 'Current stored Plan operation and Delta admission')
    flow.add_node('owner', owner_id + ' current executable owner', kind='authority')
    for row in actions:
        key = 'action_' + row['name']
        flow.add_node(key, row['name'], kind='action')
        flow.add_edge('sdk', key, row['permission'])
        if row['requires_delta']:
            flow.add_edge('delta', key, 'required current operation contract')
        flow.add_edge(key, 'writer' if row['mutates'] else 'read', row['workflow'])
    flow.add_edge('writer', 'owner', 'owned mutation and attributed result')
    flow.add_edge('owner', 'read', 'published state')
    mmd, dot, topology = flow.render_pair()
    outputs[folder + '/workflow.mmd'] = mmd.encode()
    outputs[folder + '/workflow.dot'] = dot.encode()
    workflow = {'schema': 'evidence-lane.authority-workflow.v4', 'owner_id': owner_id,
        'source_package_folder': folder, 'storage': storage, 'actions': actions, 'views': list(views),
        'runtime_modules': runtime_modules, 'topology': topology,
        'graph_role': 'source_package_executable_routes', 'installed_execution_claimed': False}
    outputs[folder + '/workflow.v4.json'] = encode(workflow)
    tools = {'schema': 'evidence-lane.authority-tooling.v4', 'owner_id': owner_id,
        'runtime': 'runtime.py', 'builder': 'builder.py:build_authority', 'reader': 'reader.py:read_authority',
        'actions': [{key: row[key] for key in ('name', 'permission', 'mutates', 'requires_delta',
            'project_required', 'profile', 'workflow', 'required_tools', 'toolchain', 'verification_checks')} for row in actions],
        'views': list(views), 'dependency_installation_inferred': False,
        'planned_actions': [row['name'] for row in actions if row['requires_delta']],
        'planned_entrypoint': 'delta_enter_planned through the owning sector builder',
        'shared_toolchain': 'Owning operation contracts and measured shared engine readiness'}
    tools_name = 'tools.json' if owner_id in {'instructions', 'canon_consequence_graph'} else stem + '.tools.json'
    outputs[folder + '/' + tools_name] = encode(tools)
    outputs[folder + '/tools.schema.json'] = schema_bytes({'type': 'object', 'required': list(tools),
        'properties': {'schema': {'const': tools['schema']}, 'owner_id': {'const': owner_id},
            'dependency_installation_inferred': {'const': False}, 'actions': {'type': 'array'}}}, owner_id + ' tooling')
    outputs[folder + '/README.md'] = (f'# {owner_id} source owner\n\n'
        f'Current executable source package: `{folder}`. Storage: {storage}. '
        'The package binds the canonical engine implementations listed in `runtime-binding.v4.json`.\n\n'
        '`builder.py` and `reader.py` use the same authenticated SDK and engine registry as skills and native MCP. '
        'Builders admit only their named ordinary mutations; planned operations retain normal stored-Plan Delta admission. '
        'Engine grants, task bindings, writer ownership, stale-contract checks and acceptance rules still apply. '
        'Queued responses are not verified completion. Instructions and Studio reads do not gain mutation privileges.\n\n'
        'Schema and workflow graphs are source projections. They are not runtime receipts or project facts. '
        'Project MMD, DOT, navigation pointers and natural artifacts remain distinct, consumer-selected lane files. '
        'The exact view contracts describe their locators, formats, freshness and refresh behavior. '
        'Tool requirements are shared references and do not prove installation.\n').encode()
    return outputs, workflow


def compile_authority_assets(registry, lane_id):
    folder = authority_package_folder(lane_id)
    lane = get_lane(lane_id)
    from evidence_lane_plugin.authority_support import authority_profile
    profile = authority_profile(lane_id)
    views = [row for row in registry.view_schemas() if row['lane_id'] == lane_id]
    outputs, workflow = compile_workflow_assets(registry, lane_id, folder,
        storage=lane.database_relative_path + ' with files and schema history in ' + lane.folder,
        runtime_modules=list(dict.fromkeys('evidence_lane_plugin.' + module for module, _ in profile.migration_bindings)), views=views)
    raw, schema = empty_schema_from_migrations(lane_id, authority_migrations(lane_id),
        foundation=LANE_SCHEMA + (RECEIPTS_SCHEMA if lane_id == 'receipts' else ''), kind='authority',
        schema_seed_rows={'registry_meta': [('schema', 'evidence-lane.source-authority-registry.v1')]} if lane_id == 'sources' else None)
    # Use the original template names while declaring the different live path.
    stem = ASSET_STEMS[lane_id]
    outputs[folder + '/' + stem + '.sqlite'] = raw
    outputs[folder + '/sqlite-schema.v4.json'] = encode(schema)
    mmd, dot, topology = schema_graph(lane_id, schema)
    outputs[folder + '/' + stem + '.mmd'] = mmd.encode()
    outputs[folder + '/' + stem + '.dot'] = dot.encode()
    workflow['schema_topology'] = topology
    outputs[folder + '/workflow.v4.json'] = encode(workflow)
    outputs[folder + '/sqlite-artifact.schema.json'] = schema_bytes({'type': 'object',
        'required': list(schema), 'properties': {'lane_id': {'const': lane_id},
            'database_role': {'const': 'unbound_schema_template'}, 'business_rows': {'const': 0},
            'project_identity_rows': {'const': 0}, 'sqlite_sha256': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}},
        lane_id + ' empty unbound schema template')
    for kind in ('mmd', 'dot'):
        outputs[folder + '/' + kind + '-artifact.schema.json'] = schema_bytes(
            ViewPublished.model_json_schema() | {'x-format': kind, 'x-lane-id': lane_id,
                'x-view-contracts': views, 'x-automatic-refresh': False}, lane_id + ' selected project graph publication')
    pointer = {'schema': 'evidence-lane.authority-pointer-contract.v4', 'lane_id': lane_id,
        'artifact_contract': lane_artifact_contract(lane_id), 'views': views,
        'role': 'Exact immutable selected-view navigation; never a second current-state authority',
        'automatic_bundle': False}
    outputs[folder + '/pointer-contract.v4.json'] = encode(pointer)
    outputs[folder + '/pointer-contract.schema.json'] = schema_bytes(ViewState.model_json_schema(), lane_id + ' selected pointer readback')
    outputs[folder + '/README.md'] += (f'\n`{stem}.sqlite` is a newly generated empty, unbound template. '
        'It contains no project identities, receipts or business records and is never opened as live state. '
        f'`{stem}.mmd` and `{stem}.dot` describe its schema. The engine applies the actual owner migrations '
        'under a writer and records schema history in the selected external lane.\n').encode()
    if lane_id == 'canon':
        consequence = folder + '/consequence_graph'
        actions = [row for row in authority_actions(registry, 'canon') if row['name'] in {
            'task_evidence_graph', 'task_evidence_inspect', 'task_evidence_result', 'task_evidence_input_request', 'task_evidence_edge_bind', 'task_evidence_edge_register'}]
        additions, _ = compile_workflow_assets(registry, 'canon_consequence_graph', consequence,
            actions=actions, storage='The parent Canon lane and its attributed events; no separate graph database',
            runtime_modules=['evidence_lane_plugin.canon_consequence_graph', 'evidence_lane_plugin.canon_task_graph'], views=views)
        additions[consequence + '/runtime.py'] = b'''"""Canon consequence/task graph uses the same owning Canon database."""
from evidence_lane_plugin.canon_consequence_graph import canon_pointer, canon_view
from evidence_lane_plugin.canon_task_graph import CanonStore, CanonTaskGraph, CanonTaskGraphRead

__all__ = ['CanonStore', 'CanonTaskGraph', 'CanonTaskGraphRead', 'canon_pointer', 'canon_view']
'''
        outputs.update(additions)
    assert AUTHORITY_SOURCE_OWNERS[lane_id] == folder.rsplit('/', 1)[1]
    return outputs
