"""Compile the original sector package's schemas, builders, readers and graphs.

Only current engine contracts are inputs. SQLite templates start empty in memory;
no project database, original v3 database or project graph is copied.
"""
from __future__ import annotations

import hashlib
import json
import operator
import sqlite3
from functools import reduce
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, create_model

from evidence_lane_plugin.adaptive_delta_entry import PlannedDeltaEnter
from evidence_lane_plugin.artifact_contract import ViewPublished, ViewState
from evidence_lane_plugin.graph_pipeline import SemanticGraph
from evidence_lane_plugin.lanes import CUSTOM_INSTANCE_PATTERN, get_lane
from evidence_lane_plugin.sdk import UUID_PATTERN, ActionRequest, ActionResponse
from evidence_lane_plugin.sector_support import (
    sector_actions,
    sector_migrations,
    sector_package_folder,
)
from evidence_lane_plugin.storage import LANE_APPLICATION_ID, LANE_SCHEMA


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def schema_bytes(value, title):
    return encode({'$schema': 'https://json-schema.org/draft/2020-12/schema', **value, 'title': title})


def _qualified_request_schema(registry, actions, lane_id):
    """Use actual closed input models, retaining action/payload discrimination."""
    models = []
    for action in actions:
        spec = registry.get(action['name'])
        default = spec.input_model.model_fields['lane_id'].default
        lane_field = (str, Field(default=lane_id if default == lane_id else ...,
            pattern=r'^(?:custom|' + CUSTOM_INSTANCE_PATTERN + r')$')) if lane_id == 'custom' else (Literal[lane_id], lane_id if default == lane_id else ...)
        arguments = create_model('SectorArgs_' + spec.name + '_' + lane_id, __base__=spec.input_model,
            lane_id=lane_field)
        models.append(create_model('SectorRequest_' + spec.name, __base__=ActionRequest,
            action=(Literal[spec.name], ...), project_id=(str, Field(pattern=UUID_PATTERN)),
            arguments=(arguments, ...)))
    if not models:
        return {'not': {}}
    if len(models) == 1:
        return models[0].model_json_schema()
    union = reduce(operator.or_, models)
    return TypeAdapter(Annotated[union, Field(discriminator='action')]).json_schema()


def _empty_schema(lane_id):
    return empty_schema_from_migrations(lane_id, sector_migrations(lane_id))


def empty_schema_from_migrations(lane_id, migrations, *, foundation=LANE_SCHEMA,
                                 kind='sector', application_id=LANE_APPLICATION_ID, schema_seed_rows=None):
    """Reject data except explicitly declared exact static schema metadata."""
    seeds = dict(schema_seed_rows or {})
    verified_seeds = set()
    with sqlite3.connect(':memory:') as connection:
        connection.execute(f'PRAGMA application_id={application_id}')
        connection.executescript(foundation)
        for migration in migrations:
            for statement in migration.statements:
                connection.execute(statement)
        tables, relations = [], []
        types = {row[1]: row[2] for row in connection.execute('PRAGMA table_list')}
        for name, sql in connection.execute("SELECT name,sql FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
            if types[name] == 'shadow':
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            rows = connection.execute('SELECT count(*) FROM ' + quoted).fetchone()[0]
            if name in seeds:
                if connection.execute('SELECT * FROM ' + quoted).fetchall() != seeds[name]:
                    raise ValueError('Schema metadata differs from its exact declaration: ' + name)
                verified_seeds.add(name)
            elif rows:
                raise ValueError('A schema template contains data: ' + name)
            columns = [{'name': row[1], 'type': row[2], 'not_null': bool(row[3]), 'primary_key': row[5]}
                       for row in connection.execute('PRAGMA table_info(' + quoted + ')')]
            tables.append({'name': name, 'type': types[name], 'sql': sql, 'rows': rows, 'columns': columns})
            relations.extend({'source': name, 'target': row[2], 'column': row[3], 'target_column': row[4]}
                             for row in connection.execute('PRAGMA foreign_key_list(' + quoted + ')'))
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or connection.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('The empty schema is inconsistent')
        connection.commit()
        raw = connection.serialize()
    if verified_seeds != set(seeds):
        raise ValueError('Declared schema metadata table is absent')
    result = {'schema': 'evidence-lane.empty-' + kind + '-schema.v4', 'lane_id': lane_id,
        'database_role': 'unbound_schema_template', 'business_rows': 0, 'project_identity_rows': 0,
        'sqlite_version': sqlite3.sqlite_version, 'sqlite_sha256': hashlib.sha256(raw).hexdigest(),
        'tables': tables, 'relations': relations, 'runtime_initialization_performed': False}
    if seeds:
        result['static_schema_metadata'] = seeds
    return raw, result


def compile_lane_assets(registry, lane_id):
    lane = get_lane(lane_id)
    folder = sector_package_folder(lane_id)
    actions = sector_actions(registry, lane_id)
    reads, writes = ([row for row in actions if row['mutates'] is selected] for selected in (False, True))
    defaults = {row['name']: registry.get(row['name']).input_model.model_fields['lane_id'].default
                for row in actions}
    outputs = {}
    for filename, selected, function in (('builder.py', writes, 'build_sector_source'),
                                         ('reader.py', reads, 'read_sector_source')):
        method = 'build_lane_sources' if filename == 'builder.py' else 'read_lane_source'
        bindings = {row['name']: defaults[row['name']] if isinstance(defaults[row['name']], str) else lane_id for row in selected}
        outputs[folder + '/' + filename] = (f'"""{lane_id}: typed binding to the authenticated shared SDK and owning engine."""\n'
            f'from evidence_lane_plugin.sector_support import {function}\n\nLANE_ID = {lane_id!r}\n'
            f'ACTION_LANES = {bindings!r}\n\ndef {method}(client, **arguments):\n'
            f'    return {function}(client, LANE_ID, ACTION_LANES, **arguments)\n\n'
            f'__all__ = ["LANE_ID", "{method}"]\n').encode()
    outputs[folder + '/builder-contract.schema.json'] = schema_bytes(
        PlannedDeltaEnter.model_json_schema() | {'x-public-action': 'delta_enter_planned',
            'x-sector-lane': lane_id, 'x-operation-actions': [row['name'] for row in writes],
            'x-project-id-required': True}, lane.display_label + ' planned builder arguments')
    outputs[folder + '/reader-contract.schema.json'] = schema_bytes(
        _qualified_request_schema(registry, reads, lane_id), lane.display_label + ' typed reader requests')
    outputs[folder + '/refresh-receipt.schema.json'] = schema_bytes(
        ActionResponse.model_json_schema(), lane.display_label + ' attributed SDK operation response')
    outputs[folder + '/lane-pointer.schema.json'] = schema_bytes(
        ViewState.model_json_schema(), lane.display_label + ' snapshot navigation and freshness readback')
    for kind in ('mmd', 'dot'):
        outputs[folder + '/' + kind + '-artifact.schema.json'] = schema_bytes(
            ViewPublished.model_json_schema() | {'x-format': kind, 'x-lane-id': lane_id,
            'x-selection-policy': 'Only explicitly selected lane formats; natural artifacts remain separate.'},
            lane.display_label + ' selected ' + kind.upper() + ' publication')

    raw, schema = _empty_schema(lane_id)
    outputs[folder + '/' + lane.sqlite_filename] = raw
    outputs[folder + '/schema-template.json'] = encode(schema)
    outputs[folder + '/sqlite-artifact.schema.json'] = schema_bytes({
        'type': 'object', 'required': ['schema', 'lane_id', 'database_role', 'business_rows', 'project_identity_rows', 'sqlite_sha256', 'tables', 'relations'],
        'properties': {'schema': {'const': schema['schema']}, 'lane_id': {'const': lane_id},
            'database_role': {'const': 'unbound_schema_template'}, 'business_rows': {'const': 0},
            'project_identity_rows': {'const': 0}, 'sqlite_sha256': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
            'tables': {'type': 'array'}, 'relations': {'type': 'array'}},
        'additionalProperties': True}, lane.display_label + ' empty schema template inspection')

    graph = SemanticGraph('schema_' + lane_id, role='AUTHORITY_TRAVERSAL')
    table_names = {row['name'] for row in schema['tables']}
    for row in schema['tables']:
        graph.add_node('t_' + row['name'], row['name'] + '\n' + ', '.join(col['name'] for col in row['columns']), kind='table')
    for row in schema['relations']:
        if row['target'] not in table_names:
            raise ValueError('Unresolved schema relationship: ' + row['target'])
        graph.add_edge('t_' + row['source'], 't_' + row['target'], row['column'])
    mmd, dot, topology = graph.render_pair()
    outputs[folder + '/' + lane.mmd_filename] = mmd.encode()
    outputs[folder + '/' + lane.dot_filename] = dot.encode()

    flow = SemanticGraph('workflow_' + lane_id, role='EXECUTABLE_WORKFLOW')
    flow.add_node('lane', lane_id + ' owning database and immutable files', kind='authority')
    flow.add_node('delta', 'Exact current Plan and normal Delta admission')
    flow.add_node('verify', 'Owning acceptance checks and verified Delta exit')
    flow.add_node('read', 'Scoped authenticated SDK read')
    for action in actions:
        identity = 'action_' + action['name']
        flow.add_node(identity, action['name'], kind='action')
        if action['mutates']:
            if not action['requires_delta']:
                raise ValueError('Declare the non-Delta sector workflow explicitly before graph generation')
            flow.add_edge('delta', identity, action['profile'])
            flow.add_edge(identity, 'lane', 'owned publication')
            flow.add_edge(identity, 'verify', ', '.join(action['verification_checks']))
        else:
            flow.add_edge('read', identity, action['permission'])
            flow.add_edge('lane', identity, 'bounded recorded evidence')
    workflow_mmd, workflow_dot, workflow_topology = flow.render_pair()
    outputs[folder + '/workflow.mmd'] = workflow_mmd.encode()
    outputs[folder + '/workflow.dot'] = workflow_dot.encode()
    views = [row for row in registry.view_schemas() if row['lane_id'] == lane_id]
    workflow = {'schema': 'evidence-lane.sector-workflow.v4', 'lane_id': lane_id,
        'source_package_folder': folder, 'external_project_folder': lane.folder,
        'actions': [{key: row[key] for key in ('name', 'profile', 'permission', 'mutates', 'requires_delta',
                    'required_tools', 'toolchain', 'verification_checks', 'read_schemas')} for row in actions],
        'views': views, 'schema_topology': topology, 'workflow_topology': workflow_topology,
        'graph_role': 'source_package_schema_and_executable_routes', 'project_data_copied': False}
    outputs[folder + '/workflow.v4.json'] = encode(workflow)
    tools = {'schema': 'evidence-lane.installed-lane-tooling.v4', 'lane_id': lane_id,
        'builder': 'builder.py:build_lane_sources', 'reader': 'reader.py:read_lane_source',
        'runtime': 'runtime.py', 'actions': workflow['actions'], 'views': views,
        'workflow': 'workflow.v4.json', 'workflow_mmd': 'workflow.mmd', 'workflow_dot': 'workflow.dot',
        'dependency_installation_inferred': False, 'runtime_state_folder': lane.folder,
        'source_package_folder': folder}
    outputs[folder + '/tools.json'] = encode(tools)
    outputs[folder + '/tools.schema.json'] = schema_bytes({'type': 'object',
        'required': list(tools), 'properties': {'schema': {'const': tools['schema']}, 'lane_id': {'const': lane_id},
            'builder': {'const': tools['builder']}, 'reader': {'const': tools['reader']},
            'actions': {'type': 'array', 'minItems': 1}, 'views': {'type': 'array'},
            'dependency_installation_inferred': {'const': False}}}, lane.display_label + ' current lane tooling')
    outputs[folder + '/README.md'] = (f'# {lane.display_label} sector package\n\n'
        f'This original source owner is `{folder}`. Live project state belongs to `{lane.folder}` '
        'under the selected external project root. The packaged SQLite is an empty, unbound schema template; '
        'the engine creates identities and applies exact migrations under its writer. It is never opened as project state.\n\n'
        '`builder.py` admits one stored, hash-bound sector Plan operation through the authenticated SDK. '
        '`reader.py` admits only this lane\'s typed read actions. Both use the same public SDK and engine; '
        '`runtime.py` exposes the actual owning migration and read functions. '
        'A queued builder response is not completion. Read the normal Delta result and acceptance receipt.\n\n'
        '`schema.sql`, `migration-history.json` and `schema-template.json` describe the actual owner migrations. '
        f'`{lane.mmd_filename}` and `{lane.dot_filename}` are matching schema traversal maps. '
        '`workflow.v4.json`, `workflow.mmd` and `workflow.dot` describe current action routes and their gates. '
        'They contain no project facts or runtime readiness assertion.\n\n'
        'Natural document/media/data files and selected project MMD/DOT/navigation pointers remain in their own lane. '
        'The view contracts in `tools.json` define each consumer, exact locator meaning, optional formats and refresh rules. '
        'Use the first-class Source Intake references for parser fidelity, exports and verified refresh procedures.\n'
        + ('\nThis package also serves registered `custom__<name>` instances. Sources owns each immutable adapter contract; '
           'every instance has its own `sectors/<lane_id>/` database, schema history, files and views. '
           'Use the existing Custom actions with exact lane and adapter selections; no project-specific code is loaded.\n'
           if lane_id == 'custom' else '')).encode()
    return outputs
