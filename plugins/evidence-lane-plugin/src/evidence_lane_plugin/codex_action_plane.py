"""Compile full retained ENV/UOP in their original action-plane owner.

The normalized domain catalogs are source policy, not execution receipts. The
compiler binds them to current action, lane, hook and tool registries, derives
the original SQLite/FTS/MMD/DOT members and never imports project databases.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from .errors import LaneError

CODEX_ACTION_PLANE_SCHEMA = 'evidence-lane.codex-action-plane.v4'
FIXED_TIME = '2000-01-01T00:00:00Z'
_IDENTIFIER = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
DOMAIN_PATHS = {'env': 'env/codex-environment-policy.v4.json',
                'uop': 'uop/codex-operation-policy.v4.json'}
DATABASE_PATHS = {'env': 'env/env_sqlite.sqlite', 'uop': 'uop/uop_sqlite.sqlite'}


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise LaneError('ENV_UOP_IDENTIFIER_INVALID', 'A packaged policy identifier is invalid.')
    return '"' + value + '"'


def _load_domain_catalog(plugin_root, role):
    from .codex_env_uop_policy import env_catalog, uop_catalog
    catalog = env_catalog() if role == 'env' else uop_catalog()
    body = {key: value for key, value in catalog.items() if key != 'catalog_sha256'}
    if (catalog.get('schema') != f'evidence-lane.{role}-codex-policy.v4'
            or _digest(body) != catalog.get('catalog_sha256')
            or catalog.get('source_model') != 'direct_current_codex_and_evidence_lane_contracts'
            or catalog.get('legacy_translation_layer') is not False
            or catalog.get('project_payload_allowed') is not False
            or catalog.get('runtime_mutation_allowed') is not False
            or not 1 <= len(catalog.get('tables', {})) <= 64):
        raise LaneError('ENV_UOP_DOMAIN_INVALID', 'The current domain policy differs from its declared contract.')
    return catalog


def _load_env_domain_catalog(plugin_root):
    return _load_domain_catalog(plugin_root, 'env')


def _load_uop_domain_catalog(plugin_root):
    return _load_domain_catalog(plugin_root, 'uop')


def _create_schema(connection, catalog):
    connection.execute('PRAGMA user_version=4')
    connection.execute('PRAGMA foreign_keys=ON')
    for name, table in catalog['tables'].items():
        columns = table['columns']
        if not 1 <= len(columns) <= 64 or len({row['name'] for row in columns}) != len(columns):
            raise LaneError('ENV_UOP_SCHEMA_INVALID', 'Policy columns must have unique bounded identities.')
        declarations = []
        for column in columns:
            kind = column['type']
            if kind not in {'TEXT', 'INTEGER', 'REAL', 'BLOB'}:
                raise LaneError('ENV_UOP_SCHEMA_INVALID', 'Policy column types must use the declared SQLite types.')
            declaration = _identifier(column['name']) + ' ' + kind
            if column['not_null']:
                declaration += ' NOT NULL'
            if column['name'].endswith('_json'):
                declaration += ' CHECK(json_valid(' + _identifier(column['name']) + '))'
            if column['name'] in {'agent_authority', 'provider_is_agent', 'provider_is_tool',
                    'authority_effect', 'can_override_env', 'can_override_project',
                    'private_content_allowed', 'authorizes_execution', 'creates_sector',
                    'mutation_allowed', 'partial_window', 'partial_window_allowed'}:
                declaration += ' CHECK(' + _identifier(column['name']) + '=0)'
            declarations.append(declaration)
        primary = sorted((row for row in columns if row['primary_key']), key=lambda row: row['primary_key'])
        if primary:
            declarations.append('PRIMARY KEY (' + ','.join(_identifier(row['name']) for row in primary) + ')')
        connection.execute('CREATE TABLE ' + _identifier(name) + '(' + ','.join(declarations) + ') STRICT')
        for row in table['rows']:
            _insert(connection, name, row)


def _insert(connection, table, row):
    names = list(row)
    connection.execute('INSERT INTO ' + _identifier(table) + '(' + ','.join(_identifier(key) for key in names)
        + ') VALUES (' + ','.join('?' for _ in names) + ')', tuple(row[key] for key in names))


def classify_action_workflow_classes(action):
    profile = action.get('profile', 'core')
    workflow = action.get('workflow', 'evidence-lane')
    if profile in {'local_code', 'github_code', 'code'}:
        return ['CODE', 'RETRIEVAL'] if not action.get('mutates') else ['CODE']
    if profile in {'docs', 'document', 'ppt', 'presentation'}:
        return ['DOCUMENT']
    if profile in {'data', 'data_excel', 'spreadsheet', 'tableau', 'power_bi', 'custom'}:
        return ['DATA']
    if profile in {'pdf_ocr', 'images_ocr'}:
        return ['OCR_MEDIA']
    if profile == 'research':
        return ['WEB_RESEARCH']
    return {
        'manage-project-sources': ['SOURCE_ROUTING', 'RETRIEVAL'],
        'retrieve-project-evidence': ['RETRIEVAL'],
        'manage-project-memory': ['RETRIEVAL'],
        'inspect-project-evidence-map': ['RETRIEVAL'],
        'select-project-tools': ['GOVERNANCE', 'RUNTIME'],
        'open-project-session': ['RUNTIME'],
        'close-project-session': ['RUNTIME'],
        'recover-project-state': ['RECOVERY'],
    }.get(workflow, ['GOVERNANCE'])


def action_event(action):
    name = action['name']
    if name in {'session_boot', 'session_resume'}:
        return 'BOOT_OR_RESUME'
    if name == 'session_exit':
        return 'EXIT_BOOT'
    if name in {'steer_preview', 'steer_submit'}:
        return 'STEER_ENTRY'
    if name in {'plan_create', 'plan_refresh'}:
        return 'PLAN_MUTATION'
    if name in {'plan_host_bind', 'plan_host_sync'}:
        return 'HOST_PLAN_PROJECTION'
    if name == 'delta_enter':
        return 'DELTA_ENTRY'
    if name in {'entry_classify', 'project_work_classify', 'task_classify'}:
        return 'PROMPT_ENTRY'
    if action.get('workflow') == 'recover-project-state':
        return 'RECOVERY'
    if not action.get('mutates'):
        return 'MID_DELTA_QUERY'
    return 'DELTA_ENTRY'


def _populate(plugin_root, env, uop, registry):
    from .capture_routing import HOOK_EVENT_ORDER
    from .hook_contract import hook_event_handler_path
    from .host_routing import HOST_MATRIX
    from .lanes import LANE_REGISTRY
    from .tool_catalog import declarations
    from .tool_routes import declared_pipeline_tools, operation_contract

    root = Path(plugin_root)
    actions = registry.schemas()
    workflows = registry.workflow_schemas()
    tools = [row for row in declarations()['entries'] if row['lifecycle'] == 'retained']
    skill_by_workflow = {row['name']: row['skill'] for row in workflows}
    metadata = {'schema': CODEX_ACTION_PLANE_SCHEMA, 'host_plane': 'CODEX_ONLY', 'codex_is_sole_agent': 'true',
        'foreign_surface_payload_allowed': 'false', 'user_instructions_take_precedence': 'true',
        'action_set_digest': _digest(actions), 'runtime_write_allowed': 'false',
        'policy_declaration_is_runtime_proof': 'false'}
    for key, value in metadata.items():
        _insert(env, 'env_authority_meta', {'key': key, 'value': value})
    for key, value in {**metadata, 'governance_only': 'true', 'can_override_env': 'false', 'can_override_project': 'false'}.items():
        _insert(uop, 'uop_authority_meta', {'key': key, 'value': value})
    for host_id, host in sorted(HOST_MATRIX.items()):
        _insert(env, 'codex_host_variant_v4', {'host_id': host_id, 'host_profile': host['family'],
            'app_variant': host['channel'], 'application_id': 'not_attested_by_policy',
            'lifetime': 'ephemeral' if host_id == 'codex_vm_ephemeral' else 'configured_profile',
            'native_mcp': int(host_id != 'unknown'), 'tunnel_policy': host['storage_policy'], 'status': 'DECLARED_PROFILE'})
        _insert(uop, 'uop_host_policy_v4', {'host_id': host_id, 'host_profile': host['family'],
            'execution_allowed': int(host_id != 'unknown'), 'reason': host['storage_policy'], 'status': 'REQUIRES_MEASURED_ROUTE'})
    for provider, vendor, runtime in [('CPU', 'NONE', 'current CPU engine'), ('NVIDIA_CUDA', 'NVIDIA', 'compatible pinned CUDA runtime'),
            ('AMD_ROCM', 'AMD', 'compatible pinned HIP runtime'),
            ('DIRECTML', 'CROSS_VENDOR', 'compatible pinned Windows DirectML runtime')]:
        _insert(env, 'env_accelerator_profile_v4', {'provider_id': provider, 'vendor_plugin': vendor, 'runtime': runtime,
            'eligible_action_classes_json': _json(['ALL'] if provider == 'CPU' else ['RETRIEVAL', 'OCR_MEDIA', 'EVALUATION']),
            'default_memory_budget_percent': 100 if provider == 'CPU' else 80,
            'selection_rule': 'Compatible observed device, exact grant, telemetry and declared CPU fallback',
            'provider_is_tool': 0, 'provider_is_agent': 0, 'status': 'DECLARED_PROVIDER'})
        _insert(uop, 'uop_accelerator_policy_v4', {'provider_id': provider, 'vendor_plugin_grant_required': int(provider != 'CPU'),
            'memory_budget_configurable': 1, 'default_memory_budget_percent': 100 if provider == 'CPU' else 80,
            'max_memory_budget_percent': 100 if provider == 'CPU' else 95, 'telemetry_required': int(provider != 'CPU'),
            'throttle_blocks_execution': 1, 'cpu_fallback_required': 1, 'authority_effect': 0, 'status': 'DECLARED_POLICY'})
    for tool in tools:
        external = tool['kind'] in {'external_service', 'mcp_tool_provider'}
        _insert(env, 'env_tool_registry_v4', {'tool_id': tool['tool_id'], 'requirement': tool['requirement'],
            'surfaces_json': _json(tool['lanes']), 'role': tool['description'], 'agent_authority': 0, 'status': 'DECLARED_NOT_EXECUTED'})
        _insert(uop, 'uop_tool_policy_v4', {'tool_id': tool['tool_id'], 'requirement': tool['requirement'],
            'grant_required': int(external), 'locality_expiry_required': int(external), 'agent_authority': 0,
            'selection_rule': 'Exact action/lane/platform/permission/readiness and declared ordered same-contract fallback', 'status': 'DECLARED_NOT_EXECUTED'})
    env.execute('CREATE TABLE env_operation_pipeline_v4(action_name TEXT NOT NULL,route_id TEXT NOT NULL,ordinal INTEGER NOT NULL,pipeline_json TEXT NOT NULL CHECK(json_valid(pipeline_json)),PRIMARY KEY(action_name,route_id)) STRICT')
    env.execute('CREATE TABLE env_mode_policy_v4(mode_id TEXT PRIMARY KEY,policy_json TEXT NOT NULL CHECK(json_valid(policy_json))) STRICT')
    from .codex_env_uop_policy import work_policy
    from .operating_modes import MODE_DEFINITIONS
    for definition in MODE_DEFINITIONS:
        policy = work_policy(definition)
        _insert(env, 'env_mode_policy_v4', {
            'mode_id': definition['id'],
            'policy_json': _json({
                'schema': 'evidence-lane.env-mode-policy.v4',
                'mode_id': definition['id'],
                'name': definition['name'],
                'aliases': list(definition['aliases']),
                'lane_templates': list(definition['lanes']),
                'work_policy': policy,
                'classification_authorizes_execution': False,
                'current_registry_required': True,
            }),
        })
        _insert(env, 'env_work_policy_v4', {
            'work_id': policy['work_id'], 'work_name': policy['work_name'],
            'lane_templates_json': _json(policy['lane_templates']),
            'scan_order': policy['scan_order'], 'unit_of_work': policy['unit_of_work'],
            'workflow_order': policy['workflow_order'], 'recursive_loop': policy['recursive_loop'],
            'validation_gate': policy['validation_gate'], 'exit_write_target': policy['exit_write_target'],
            'action_classes_json': _json(policy['action_classes']),
            'ci_cd_required': int(policy['ci_cd_required']), 'status': 'ACTIVE'})
        _insert(uop, 'uop_work_classification_policy_v4', {
            'work_id': policy['work_id'],
            'selection_rule': 'explicit user selection or honestly labeled prompt classification',
            'authorizes_execution': 0, 'creates_sector': 0, 'status': 'ACTIVE'})
    for action in actions:
        spec = registry.get(action['name'])
        pipeline = operation_contract(spec)
        internal = {'module': spec.handler.__module__, 'handler': spec.handler.__qualname__,
                    'registry_action': action['name'], 'execution_owner': 'persistent_engine'}
        ordered_tools = list(dict.fromkeys(tool for route in pipeline['routes'] for tool in declared_pipeline_tools(route)))
        binding = {'action_name': action['name'], 'workflow_classes_json': _json(classify_action_workflow_classes(action)),
            'owner_skill': skill_by_workflow[action['workflow']], 'internal_sdk_json': _json(internal),
            'mcp_json': _json({'server': 'evidence-lane', 'tool': action['name'], 'transport': 'thin_native_adapter'}),
            'skill_workflows_json': _json([action['workflow']]), 'entry_event': action_event(action),
            'ordered_tools_json': _json(ordered_tools), 'schema_sha256': _digest(action), 'status': 'REGISTERED_CONTRACT'}
        binding['binding_sha256'] = _digest(binding)
        _insert(env, 'env_action_binding_v4', binding)
        _insert(env, 'env_sdk_action_binding_v4', {'action_name': action['name'], 'internal_sdk_json': _json(internal),
            'outer_route_json': _json({'owner': 'sdk.EvidenceLaneClient', 'API': '/v4/action', 'operation': action['name']}), 'status': 'REGISTERED_CONTRACT'})
        _insert(env, 'env_mcp_action_binding_v4', {'action_name': action['name'], 'server_identity': 'evidence-lane',
            'tool_name': action['name'], 'status': 'REGISTERED_CONTRACT'})
        _insert(uop, 'uop_action_policy_v4', {'action_name': action['name'], 'read_only': int(not action['mutates']),
            'destructive': int(action['permission'] in {'restore', 'delete'}), 'idempotent': 0,
            'requires_exact_user_decision': 0, 'direct_purge_required': 0,
            'authority_effects_json': _json({key: action[key] for key in ('profile', 'workflow', 'permission', 'mutates',
                'project_required', 'requires_delta', 'queued', 'queryable_in_delta', 'studio_read', 'cross_project_read',
                'required_tools', 'worker_operations', 'path_fields', 'verification_checks')}), 'status': 'REGISTERED_CONTRACT'})
        for ordinal, route in enumerate(pipeline['routes'], 1):
            _insert(env, 'env_operation_pipeline_v4', {'action_name': action['name'], 'route_id': route['route_id'],
                'ordinal': ordinal, 'pipeline_json': _json(route)})
    for lane_id, lane in sorted(LANE_REGISTRY.items()):
        aliases = {lane_id, *lane.aliases}
        if lane_id == 'chat_lineage':
            aliases.add('chatlineage')
        selected = [row for row in actions if row['profile'] in aliases]
        tools_for_lane = list(dict.fromkeys(tool for action in selected
            for route in operation_contract(registry.get(action['name']))['routes'] for tool in declared_pipeline_tools(route)))
        row = {'lane_id': lane_id, 'source_types_json': _json(lane.source_types), 'parser_id': lane.parser_id,
            'chunker_version': lane.chunker_version, 'fts_table': lane.fts_table,
            'ordered_tools_json': _json(tools_for_lane), 'status': 'DECLARED_OWNING_LANE',
            'action_names_json': _json([action['name'] for action in selected]),
            'action_classes_json': _json(list(dict.fromkeys(value for action in selected
                for value in classify_action_workflow_classes(action))))}
        row['binding_sha256'] = _digest({**row, 'lane': lane.as_dict()})
        _insert(env, 'env_lane_binding_v4', row)
        _insert(env, 'env_source_lane_classification_v4', {
            'lane_id': lane_id, 'lane_kind': lane.kind,
            'source_types_json': _json(list(lane.source_types)), 'owner_folder': lane.folder,
            'status': 'CURRENT_OWNER'})
    for workflow in workflows:
        folder = root / 'skills' / workflow['skill']
        members = [p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts] if folder.is_dir() else []
        _insert(env, 'env_skill_binding_v4', {'skill_name': workflow['skill'], 'description': workflow['description'],
            'workflow_json': _json(workflow), 'member_count': len(members), 'routing_manifest_sha256': _digest(workflow),
            'status': 'REGISTERED_WORKFLOW_NOT_NATIVE_PROOF'})
        _insert(env, 'env_workflow_policy_v4', {
            'workflow_id': workflow['name'], 'skill_name': workflow['skill'],
            'title': workflow['title'], 'description': workflow['description'],
            'action_names_json': _json([row['name'] for row in workflow['actions']]),
            'status': workflow['availability']})
    for ordinal, event in enumerate(HOOK_EVENT_ORDER, 1):
        event_path = hook_event_handler_path(event)
        binding = {'event': event, 'entrypoint': event_path, 'transport': 'authenticated_bound_capture',
                   'workload': 'source_event_and_entry_classification' if event == 'UserPromptSubmit' else 'documented_event_workload'}
        _insert(env, 'env_hook_binding_v4', {'event_name': event, 'event_number': ordinal, 'handler_count': 1,
            'event_path': event_path, 'event_sha256': _digest(binding), 'status': 'DECLARED_NATIVE_EVENT_NOT_EXECUTION_PROOF'})
    return {'action_count': len(actions), 'tool_count': len(tools), 'lane_count': len(LANE_REGISTRY),
            'skill_count': len(workflows), 'hook_event_count': len(HOOK_EVENT_ORDER),
            'host_variant_count': len(HOST_MATRIX), 'work_class_count': len(MODE_DEFINITIONS),
            'workflow_stage_count': env.execute('SELECT count(*) FROM env_codex_workflow_stage_v4').fetchone()[0],
            'required_gate_count': uop.execute('SELECT count(*) FROM uop_required_gate_v4').fetchone()[0]}


def _build_graph(connection, role):
    from .graph_pipeline import SemanticGraph
    graph = SemanticGraph(role + '_operating_framework', direction='TB', role='AUTHORITY_TRAVERSAL')
    graph.add_node('ROOT', role.upper() + ': locked operating policy and current executable bindings', 'root')
    table_names = [r[0] for r in connection.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")]
    for table in table_names:
        if table.endswith(('_build_receipt', '_authority_meta')):
            continue
        table_id = 'TABLE_' + table
        graph.begin_group('GROUP_' + table, table.replace('_v4', '').replace('_', ' '))
        graph.add_node(table_id, table, 'semantic')
        graph.add_edge('ROOT', table_id, 'owns policy rows')
        for ordinal, raw in enumerate(connection.execute('SELECT * FROM ' + _identifier(table)), 1):
            row = dict(raw)
            identity = next((row[k] for k in (
                'action_name', 'work_id', 'stage_id', 'edge_id', 'gate_id', 'event_id',
                'permission', 'task_state', 'policy_id', 'project_class', 'lane_id',
                'workflow_id', 'skill_name', 'tool_id', 'binding_id', 'provider_id',
                'host_id', 'key') if k in row), ordinal)
            label = next((str(row[k]) for k in (
                'label', 'rule', 'requirement', 'reason', 'role', 'stage', 'status') if k in row), str(identity))
            node_id = 'ROW_' + table + '_' + str(ordinal)
            graph.add_node(node_id, str(identity) + '\n' + label[:240], 'semantic')
            graph.add_edge(table_id, node_id, 'declares')
        graph.end_group()
    if role == 'env':
        stages = connection.execute(
            'SELECT stage_id,label FROM env_codex_workflow_stage_v4 ORDER BY ordinal'
        ).fetchall()
        lookup = {row['stage_id']: 'CODEX_' + row['stage_id'] for row in stages}
        graph.begin_group('CURRENT_CODEX_FLOW', 'Current Codex and Evidence Lane workflow')
        for row in stages:
            graph.add_node(lookup[row['stage_id']], row['label'], 'semantic')
        for row in connection.execute(
                'SELECT source_stage,target_stage FROM env_codex_workflow_edge_v4 ORDER BY edge_id'):
            if row['source_stage'] not in lookup or row['target_stage'] not in lookup:
                raise LaneError('ENV_WORKFLOW_EDGE_INVALID', 'Every current workflow edge must resolve both stages.')
            graph.add_edge(lookup[row['source_stage']], lookup[row['target_stage']])
        graph.end_group()
    else:
        graph.begin_group('CURRENT_OPERATION_GATES', 'Current operation admission gates')
        for row in connection.execute('SELECT gate_id,rule FROM uop_required_gate_v4 ORDER BY gate_id'):
            node_id = 'GATE_' + row['gate_id']
            graph.add_node(node_id, row['gate_id'] + '\n' + row['rule'], 'semantic')
            graph.add_edge('ROOT', node_id, 'requires')
        graph.end_group()
    return graph


def _store_graph(connection, role, graph):
    # Original semantic graph model/renderer remains the sole MMD/DOT producer.
    mmd, dot, receipt = graph.render_pair()
    connection.executescript('''
        CREATE TABLE semantic_graph_node_v4(node_id TEXT PRIMARY KEY,label TEXT NOT NULL,node_kind TEXT NOT NULL,group_id TEXT,ordinal INTEGER NOT NULL UNIQUE) STRICT;
        CREATE TABLE semantic_graph_edge_v4(edge_id TEXT PRIMARY KEY,source_node_id TEXT NOT NULL REFERENCES semantic_graph_node_v4(node_id),target_node_id TEXT NOT NULL REFERENCES semantic_graph_node_v4(node_id),label TEXT,conditional INTEGER NOT NULL,ordinal INTEGER NOT NULL UNIQUE) STRICT;
        CREATE TABLE semantic_graph_group_v4(group_id TEXT PRIMARY KEY,label TEXT NOT NULL,direction TEXT NOT NULL,ordinal INTEGER NOT NULL UNIQUE) STRICT;
        CREATE TABLE semantic_graph_render_receipt_v4(authority_id TEXT PRIMARY KEY,semantic_topology_sha256 TEXT NOT NULL,mmd_sha256 TEXT NOT NULL,dot_sha256 TEXT NOT NULL,receipt_json TEXT NOT NULL CHECK(json_valid(receipt_json))) STRICT;
    ''')
    for ordinal, row in enumerate(graph.nodes, 1):
        _insert(connection, 'semantic_graph_node_v4', {'node_id': row.node_id, 'label': row.label,
            'node_kind': row.kind, 'group_id': row.group_id, 'ordinal': ordinal})
    for ordinal, row in enumerate(graph.edges, 1):
        _insert(connection, 'semantic_graph_edge_v4', {'edge_id': 'edge_' + str(ordinal), 'source_node_id': row.source,
            'target_node_id': row.target, 'label': row.label, 'conditional': int(row.conditional), 'ordinal': ordinal})
    for ordinal, row in enumerate(graph.groups, 1):
        _insert(connection, 'semantic_graph_group_v4', {'group_id': row.group_id, 'label': row.label,
            'direction': row.direction, 'ordinal': ordinal})
    _insert(connection, 'semantic_graph_render_receipt_v4', {'authority_id': role,
        'semantic_topology_sha256': receipt['semantic_topology_sha256'],
        'mmd_sha256': hashlib.sha256(mmd.encode()).hexdigest(), 'dot_sha256': hashlib.sha256(dot.encode()).hexdigest(),
        'receipt_json': _json(receipt)})
    return mmd.encode(), dot.encode(), receipt


def build_action_plane_assets(plugin_root, registry):
    from .sqlite_indexing import rebuild_connection_authority_index
    catalogs = {role: _load_domain_catalog(plugin_root, role) for role in ('env', 'uop')}
    connections = {role: sqlite3.connect(':memory:') for role in catalogs}
    assets = {}; receipts = {}
    try:
        for role, connection in connections.items():
            connection.row_factory = sqlite3.Row
            _create_schema(connection, catalogs[role])
        counts = _populate(plugin_root, connections['env'], connections['uop'], registry)
        for role, connection in connections.items():
            graph = _build_graph(connection, role)
            mmd, dot, graph_receipt = _store_graph(connection, role, graph)
            # Index actual current policy tables only, not derived graph copies.
            index_tables = sorted(catalogs[role]['tables'])
            index_tables = [name for name in index_tables if not name.endswith('_build_receipt')]
            if role == 'env':
                index_tables += ['env_operation_pipeline_v4']
            index_receipt = rebuild_connection_authority_index(connection, authority_id=role,
                table_names=index_tables, recorded_at=FIXED_TIME, reset_receipts=True)
            table_count = connection.execute("SELECT count(*) FROM sqlite_schema WHERE type='table'").fetchone()[0]
            receipt = {'schema': CODEX_ACTION_PLANE_SCHEMA, 'role': role, 'counts': counts, 'table_count': table_count,
                'domain_catalog_sha256': catalogs[role]['catalog_sha256'], 'action_set_digest': _digest(registry.schemas()),
                'graph': graph_receipt, 'index': index_receipt, 'scope': 'clean_policy_package_generation',
                'runtime_execution_claimed': False, 'project_payload_copied': False, 'recorded_at': FIXED_TIME}
            row = {'sequence': 1, 'table_count': table_count, 'action_count': counts['action_count'],
                'tool_count': counts['tool_count'], 'host_variant_count': counts['host_variant_count'],
                'foreign_surface_row_count': 0, 'receipt_json': _json(receipt), 'receipt_sha256': _digest(receipt),
                'recorded_at': FIXED_TIME}
            if role == 'env':
                row.update(lane_count=counts['lane_count'], skill_count=counts['skill_count'], hook_event_count=counts['hook_event_count'])
            else:
                row['gate_count'] = connection.execute('SELECT count(*) FROM uop_required_gate_v4').fetchone()[0]
            _insert(connection, role + '_action_plane_build_receipt', row)
            connection.commit()
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)] and [r[0] for r in connection.execute('PRAGMA integrity_check')] != ['ok']:
                raise LaneError('ENV_UOP_BUILD_INTEGRITY', 'The generated policy database failed integrity validation.')
            if connection.execute('PRAGMA foreign_key_check').fetchone():
                raise LaneError('ENV_UOP_BUILD_FOREIGN_KEY', 'A generated policy reference is unresolved.')
            assets[DATABASE_PATHS[role]] = connection.serialize()
            assets[f'{role}/{role}_mmd.mmd'] = mmd
            assets[f'{role}/{role}_mmd.dot'] = dot
            receipts[role] = receipt
        for role, catalog in catalogs.items():
            assets[DOMAIN_PATHS[role]] = (json.dumps(catalog, indent=2, ensure_ascii=False) + '\n').encode()
        assets['env/SOURCE_PACKET_AUDIT.json'] = (json.dumps({'schema': CODEX_ACTION_PLANE_SCHEMA,
            'authority_source': 'direct_current_codex_and_plugin_contracts',
            'catalogs': {role: value['catalog_sha256'] for role, value in catalogs.items()},
            'counts': counts, 'generated': receipts, 'legacy_translation_layer': False,
            'predecessor_policy_loaded': False, 'predecessor_database_copied': False,
            'project_payload_copied': False, 'native_execution_claimed': False}, indent=2) + '\n').encode()
        return assets
    finally:
        for connection in connections.values():
            connection.close()


def rebuild_codex_action_planes(plugin_root, *, registry, check=False):
    """Use the original rebuild entrypoint for the complete locked package."""
    from .env_uop_graph import regenerate_env_uop_authorities
    return regenerate_env_uop_authorities(plugin_root, registry=registry, check=check)


__all__ = [
    'CODEX_ACTION_PLANE_SCHEMA',
    'DATABASE_PATHS',
    'action_event',
    'build_action_plane_assets',
    'classify_action_workflow_classes',
    'rebuild_codex_action_planes',
]
