"""Conditional Code relationship exports over exact indexed snapshots."""
from __future__ import annotations

import json

from .code_profile_schema import code_migrations
from .lane_contract import LaneView, ViewGraph


def code_graph(store, scope, lane_id):
    from .code_profile import _snapshot, fact_name, read_current, snapshot_relations
    graph = ViewGraph(store.project_id, scope)
    current = read_current(store, lane_id)
    selected = [row for row in current['scopes'] if scope.query is None or row['scope_id'] == scope.query]
    if not selected:
        return graph.result()
    lane = store.lane(lane_id)
    repo = graph.node('code_repo', lane_id, 'Indexed source repository',
                      locator={'lane_id': lane_id, 'source_root': str(store.source_root)}) if selected else None
    for snapshot in selected:
        if len(graph.nodes) >= scope.node_limit:
            graph.truncated = True
            break
        snapshot_id = snapshot['snapshot_id']
        _, manifest = _snapshot(lane, snapshot_id)
        artifact = graph.node('project_artifact', snapshot_id, 'Code snapshot manifest',
            locator={'snapshot_id': snapshot_id, 'manifest_object': snapshot['manifest_object']})
        graph.edge(repo, artifact, 'PUBLISHES_INDEX')
        files = manifest['files'][:scope.node_limit + 1]
        mapping = {}
        for row in files:
            file_node = graph.node('code_file', snapshot_id + ':' + row['path'], row['path'], state=row['parser_state'],
                locator={'snapshot_id': snapshot_id, 'path': row['path'], 'sha256': row['sha256']})
            mapping[row['path']] = file_node
            graph.edge(repo, file_node, 'CONTAINS')
            source_facts = json.loads(lane.read_object(row['facts_digest']))
            for fact_kind, kind, relation in (('symbol', 'code_symbol', 'DECLARES'),
                    ('route', 'app_route', 'ROUTES'), ('dependency', 'dependency_item', 'DEPENDS_ON')):
                for ordinal, fact in enumerate(source_facts[fact_kind][:scope.node_limit + 1]):
                    node = graph.node(kind, snapshot_id + ':' + row['path'] + ':' + fact_kind + ':' + str(ordinal), fact_name(fact),
                        locator={'snapshot_id': snapshot_id, 'path': row['path'], 'line': fact.get('start_line', fact.get('line_number', 1)) or 1})
                    graph.edge(file_node, node, relation)
        edges = sorted((row for row in snapshot_relations(lane, manifest) if row[4] == 'unique_static_path'),
                       key=lambda row: (row[0], row[3], row[1]))
        for edge in edges[:scope.edge_limit + 1]:
            graph.edge(mapping.get(edge[0]), mapping.get(edge[2]), 'IMPORTS',
                       evidence={'basis': 'static_unique_literal_path', 'line': edge[3]})
        reference = manifest['git_reference']
        if reference:
            commit = graph.node('git_commit', reference['snapshot_id'], 'Sources Git history', locator=reference)
            graph.edge(repo, commit, 'REFERENCES_HISTORY')
    graph.truncated |= current.get('truncated', False)
    return graph.result()


def code_pointer(binding, graph, files):
    return {'schema': 'evidence-lane.code-locator-map.v4', 'snapshot_binding': binding,
        'locators': {node['key']: {'node_id': node['id'], **node['locator']} for node in graph['nodes']},
        'artifacts': files, 'static_relationship_completeness': False}


def register_code_views(engine):
    from .code_profile import read_current
    def view(lane_id):
        def producer(store, scope):
            return code_graph(store, scope, lane_id)

        def head(store):
            return {lane_id: read_current(store, lane_id)}

        return LaneView(lane_id + '.relationships', lane_id, 'sectors/' + lane_id,
            'Repository, exact source files, symbols, routes, dependencies and attributed Sources Git references; static import edges only.',
            producer, (lane_id.replace('_', ''),), code_migrations(lane_id),
            mmd_filename=lane_id + '.mmd', dot_filename=lane_id + '.dot',
            pointer_filename=lane_id + '.pointer.json', pointer=code_pointer, supports_query=True,
            node_kinds=('code_repo', 'code_file', 'code_symbol', 'app_route', 'dependency_item', 'git_commit', 'project_artifact'),
            edge_kinds=('CONTAINS', 'DECLARES', 'ROUTES', 'DEPENDS_ON', 'IMPORTS', 'REFERENCES_HISTORY', 'PUBLISHES_INDEX'),
            head_reader=head)
    for lane_id in ('local_code', 'github_code'):
        engine.registry.register_view(view(lane_id))
