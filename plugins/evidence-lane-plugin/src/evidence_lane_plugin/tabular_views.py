"""Separate workbook and dataset relationship views with exact locators."""
from .lane_contract import LaneView, ViewGraph
from .tabular_schema import tabular_migrations


def graph_for(store, scope, lane_id):
    from .tabular_contracts import Selection, model_for
    from .tabular_profile import current, read_snapshot
    graph = ViewGraph(store.project_id, scope)
    selected = current(store, model_for(lane_id, Selection)())
    for row in selected['files']:
        if scope.query and scope.query != row['source_id']:
            continue
        manifest, facts = read_snapshot(store, lane_id, row['snapshot_id'])
        source = graph.node('source', row['snapshot_id'], manifest['logical_name'], locator={
            'lane_id': lane_id, 'snapshot_id': row['snapshot_id'], 'source_id': row['source_id'], 'sha256': manifest['raw_object']})
        tables, formula_nodes = {}, []
        for item in facts['items']:
            if item['kind'] not in {'sheet', 'table', 'column', 'formula', 'query'}:
                continue
            locator = {'lane_id': lane_id, 'snapshot_id': row['snapshot_id'], 'item_id': item['item_id'],
                       'part': item['part'], 'ordinal': item['ordinal'], 'kind': item['kind']}
            if item['kind'] == 'formula':
                locator.update(sheet=item['sheet'], cell=item['cell'], dependency_resolution='syntactic_A1_only')
            child = graph.node(item['kind'], row['snapshot_id'] + ':' + item['item_id'],
                (item.get('name') or item.get('cell') or item['text'])[:100], locator=locator)
            parent = tables.get(item['part'], source) if item['kind'] in {'column', 'formula'} else source
            graph.edge(parent, child, 'CONTAINS')
            if item['kind'] in {'sheet', 'table'}:
                tables[item.get('name', item['part'])] = child
                tables[item['part']] = child
            if item['kind'] == 'formula':
                formula_nodes.append((child, item))
            if graph.truncated:
                break
        for child, item in formula_nodes:
            for dependency in item['dependencies']:
                target = graph.node('reference', row['snapshot_id'] + ':' + dependency['sheet'] + '!' + dependency['range'],
                    dependency['sheet'] + '!' + dependency['range'], locator={'lane_id': lane_id, 'snapshot_id': row['snapshot_id'],
                        'sheet': dependency['sheet'], 'range': dependency['range'], 'resolution': 'syntactic_reference_not_evaluated'})
                graph.edge(child, target, 'REFERENCES')
        for value in manifest['inputs']:
            parent = graph.node('source', value['snapshot_id'], 'Input snapshot', locator=value)
            graph.edge(parent, source, 'DERIVES')
        if graph.truncated:
            break
    graph.truncated |= selected.get('truncated', False)
    return graph.result()


def excel_graph(store, scope):
    return graph_for(store, scope, 'data_excel')


def data_graph(store, scope):
    return graph_for(store, scope, 'data')


def pointer(binding, graph, files):
    return {'schema': 'evidence-lane.tabular-locators.v4', 'snapshot_binding': binding,
        'locators': {node['key']: {'node_id': node['id'], **node['locator']} for node in graph['nodes']},
        'artifacts': files, 'formula_evaluation_inferred': False, 'external_connections_opened': False}


def register_tabular_views(engine):
    from .tabular_contracts import Selection, model_for
    from .tabular_profile import current
    for lane_id, producer, meaning in (
        ('data_excel', excel_graph, 'Workbook sheets and exact formula cells with syntactic A1 reference ranges; not a calculated dependency model.'),
        ('data', data_graph, 'Dataset schema, columns and recorded transformation input snapshots in the separate structured-data lane.')):
        def heads(selected):
            return lambda store: {selected: current(store, model_for(selected, Selection)())}
        engine.registry.register_view(LaneView(lane_id + '.structure', lane_id, lane_id,
            meaning, producer, (lane_id,), tabular_migrations(lane_id),
            mmd_filename=lane_id + '.mmd', dot_filename=lane_id + '.dot', pointer_filename=lane_id + '.pointer.json',
            pointer=pointer, supports_query=True, node_kinds=('source', 'sheet', 'table', 'column', 'formula', 'query', 'reference'),
            edge_kinds=('CONTAINS', 'REFERENCES', 'RELATES', 'DERIVES'), head_reader=heads(lane_id)))
